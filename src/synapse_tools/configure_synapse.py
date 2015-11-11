"""Update the synapse configuration file and restart synapse if anything has
changed."""

import filecmp
import json
import os
import shutil
import subprocess
import tempfile

import yaml
from environment_tools.type_utils import get_current_location
from paasta_tools.marathon_tools import get_all_namespaces


SYNAPSE_TOOLS_CONFIG_PATH = '/etc/synapse/synapse-tools.conf.json'


def get_config():
    with open(SYNAPSE_TOOLS_CONFIG_PATH) as synapse_config:
        return json.load(synapse_config)

SYNAPSE_RESTART_COMMAND = ['service', 'synapse', 'restart']

ZOOKEEPER_TOPOLOGY_PATH = '/nail/etc/zookeeper_discovery/infrastructure/local.yaml'

HAPROXY_PATH = '/usr/bin/haproxy-synapse'
HAPROXY_CONFIG_PATH = '/var/run/synapse/haproxy.cfg'
HAPROXY_SOCKET_FILE_PATH = '/var/run/synapse/haproxy.sock'
HAPROXY_PID_FILE_PATH = '/var/run/synapse/haproxy.pid'
FILE_OUTPUT_PATH = '/var/run/synapse/services'

# Command used to start/reload haproxy.   Note that we touch the pid file first
# in case it doesn't exist;  otherwise the reload will fail.
HAPROXY_RELOAD_CMD = 'touch %s && PID=$(cat %s) && %s -f %s -p %s -sf $PID' % (
    HAPROXY_PID_FILE_PATH, HAPROXY_PID_FILE_PATH, HAPROXY_PATH,
    HAPROXY_CONFIG_PATH, HAPROXY_PID_FILE_PATH)
# Hack to fix SRV-2141 and OPS-8144 until we can get a proper solution
HAPROXY_RELOAD_WITH_SLEEP = '%s && sleep 0.010' % (HAPROXY_RELOAD_CMD)
HAPROXY_PROTECT_CMD = "sudo /usr/bin/synapse_qdisc_tool protect bash -c '%s'"
HAPROXY_PROTECTED_RELOAD_CMD = HAPROXY_PROTECT_CMD % HAPROXY_RELOAD_WITH_SLEEP

# Global maximum number of connections.
MAXIMUM_CONNECTIONS = 10000

HACHECK_PORT = 6666


def get_zookeeper_topology():
    with open(ZOOKEEPER_TOPOLOGY_PATH) as fp:
        zookeeper_topology = yaml.load(fp)
    zookeeper_topology = [
        '%s:%d' % (entry[0], entry[1]) for entry in zookeeper_topology]
    return zookeeper_topology


def generate_base_config(synapse_tools_config):
    haproxy_inter = synapse_tools_config.get('haproxy.defaults.inter', '10m')
    base_config = {
        # We'll fill this section in
        'services': {},
        'file_output': {'output_directory': FILE_OUTPUT_PATH},
        'haproxy': {
            'bind_address': synapse_tools_config['bind_addr'],
            'restart_interval': 60,
            'restart_jitter': 0.1,
            'state_file_path': '/var/run/synapse/state.json',
            'state_file_ttl': 30 * 60,
            'reload_command': HAPROXY_PROTECTED_RELOAD_CMD,
            'socket_file_path': HAPROXY_SOCKET_FILE_PATH,
            'config_file_path': HAPROXY_CONFIG_PATH,
            'do_writes': True,
            'do_reloads': True,
            'do_socket': True,

            'global': [
                'daemon',
                'maxconn %d' % MAXIMUM_CONNECTIONS,
                'stats socket %s level admin' % HAPROXY_SOCKET_FILE_PATH,

                # Default of 16k is too small and causes HTTP 400 errors
                'tune.bufsize 32768',

                # Add random jitter to checks
                'spread-checks 50',

                # Send syslog output to syslog2scribe
                'log 127.0.0.1:1514 daemon info',
                'log-send-hostname'
            ],

            'defaults': [
                # Various timeout values
                'timeout connect 200ms',
                'timeout client 1000ms',
                'timeout server 1000ms',

                # On failure, try a different server
                'retries 1',
                'option redispatch',

                # The server with the lowest number of connections receives the
                # connection
                'balance leastconn',

                # Assume it's an HTTP service
                'mode http',

                # Actively close connections to prevent old HAProxy instances
                # from hanging around after restarts
                'option forceclose',

                # Sometimes our headers contain invalid characters which would
                # otherwise cause HTTP 400 errors
                'option accept-invalid-http-request',

                # Use the global logging defaults
                'log global',

                # Log any abnormal connections at 'error' severity
                'option log-separate-errors',

                # Normally just check at <inter> period in order to minimize load
                # on individual services.  However, if we get anything other than
                # a 100 -- 499, 501 or 505 response code on user traffic then
                # force <fastinter> check period.
                #
                # NOTES
                #
                # * This also requires 'check observe layer7' on the server
                #   options.
                # * When 'on-error' triggers a check, it will only occur after
                #   <fastinter> delay.
                # * Under the assumption of 100 client machines each
                #   healthchecking a service instance:
                #
                #     10 minute <inter>     -> 0.2qps
                #     30 second <downinter> -> 3.3qps
                #     30 second <fastinter> -> 3.3qps
                #
                # * The <downinter> checks should only occur when Zookeeper is
                #   down; ordinarily Nerve will quickly remove a backend if it
                #   fails its local healthcheck.
                # * The <fastinter> checks may occur when a service is generating
                #   errors but is still passing its healthchecks.
                ('default-server on-error fastinter error-limit 1'
                 ' inter {inter} downinter 30s fastinter 30s'
                 ' rise 1 fall 2'.format(inter=haproxy_inter)),
            ],

            'extra_sections': {
                'listen stats': [
                    'bind :3212',
                    'mode http',
                    'stats enable',
                    'stats uri /',
                    'stats refresh 1m',
                    'stats show-node',
                ]
            }
        }
    }
    return base_config


def generate_configuration(synapse_tools_config, zookeeper_topology, services):
    synapse_config = generate_base_config(synapse_tools_config)

    for (service_name, service_info) in services:
        if service_info.get('proxy_port') is None:
            continue

        synapse_config['services'][service_name] = haproxy_cfg_for_service(
            service_name,
            service_info,
            zookeeper_topology)

    return synapse_config


def haproxy_cfg_for_service(service_name, service_info, zookeeper_topology):
    proxy_port = service_info['proxy_port']

    # If the service sets one timeout but not the other, set both
    # as per haproxy best practices.
    default_timeout = max(
        service_info.get('timeout_client_ms'),
        service_info.get('timeout_server_ms')
    )

    # Server options
    mode = service_info.get('mode', 'http')
    if mode == 'http':
        server_options = 'check port %d observe layer7' % HACHECK_PORT
    else:
        server_options = 'check port %d observe layer4' % HACHECK_PORT

    # Frontend options
    frontend_options = []
    timeout_client_ms = service_info.get(
        'timeout_client_ms', default_timeout
    )
    if timeout_client_ms is not None:
        frontend_options.append('timeout client %dms' % timeout_client_ms)

    if mode == 'http':
        frontend_options.append('capture request header X-B3-SpanId len 64')
        frontend_options.append('capture request header X-B3-TraceId len 64')
        frontend_options.append('capture request header X-B3-ParentSpanId len 64')
        frontend_options.append('capture request header X-B3-Flags len 10')
        frontend_options.append('capture request header X-B3-Sampled len 10')
        frontend_options.append('option httplog')
    elif mode == 'tcp':
        frontend_options.append('option tcplog')

    # backend options
    backend_options = []

    extra_headers = service_info.get('extra_headers', {})
    for header, value in extra_headers.iteritems():
        backend_options.append('reqadd %s:\ %s' % (header, value))

    # Listen options
    listen_options = []

    # hacheck healthchecking
    # Note that we use a dummy port value of '0' here because HAProxy is
    # passing in the real port using the X-Haproxy-Server-State header.
    # See SRV-1492 / SRV-1498 for more details.
    port = 0
    extra_healthcheck_headers = service_info.get('extra_healthcheck_headers', {})

    if len(extra_healthcheck_headers) > 0:
        healthcheck_base = 'HTTP/1.1'
        headers_string = healthcheck_base + ''.join(r'\r\n%s:\ %s' % (k, v) for (k, v) in extra_healthcheck_headers.iteritems())
    else:
        headers_string = ""

    healthcheck_uri = service_info.get('healthcheck_uri', '/status')
    healthcheck_string = r'option httpchk GET /%s/%s/%d/%s %s' % \
        (mode, service_name, port, healthcheck_uri.lstrip('/'), headers_string)

    healthcheck_string = healthcheck_string.strip()
    listen_options.append(healthcheck_string)

    listen_options.append('http-check send-state')

    if mode == 'tcp':
        listen_options.append('mode tcp')

    retries = service_info.get('retries')
    if retries is not None:
        listen_options.append('retries %d' % retries)

    allredisp = service_info.get('allredisp')
    if allredisp is not None and allredisp:
        listen_options.append('option allredisp')

    timeout_connect_ms = service_info.get('timeout_connect_ms')
    if timeout_connect_ms is not None:
        listen_options.append('timeout connect %dms' % timeout_connect_ms)

    timeout_server_ms = service_info.get(
        'timeout_server_ms', default_timeout
    )
    if timeout_server_ms is not None:
        listen_options.append('timeout server %dms' % timeout_server_ms)

    discover_type = service_info.get('discover', 'region')
    location = get_current_location(discover_type)

    discovery = {
        'method': 'zookeeper',
        'path': '/nerve/%s:%s/%s' % (discover_type, location, service_name),
        'hosts': zookeeper_topology,
    }

    chaos = service_info.get('chaos')
    if chaos:
        frontend_chaos, discovery = chaos_options(chaos, discovery)
        frontend_options.extend(frontend_chaos)

    # Now write the actual synapse service entry
    service = {
        'default_servers': [],
        # See SRV-1190
        'use_previous_backends': False,
        'discovery': discovery,
        'haproxy': {
            'port': '%d' % proxy_port,
            'server_options': server_options,
            'frontend': frontend_options,
            'listen': listen_options,
            'backend': backend_options
        }
    }

    return service


def chaos_options(chaos_dict, discovery_dict):
    """ Return a tuple of
    (additional_frontend_options, replacement_discovery_dict) """

    chaos_entries = merge_dict_for_my_grouping(chaos_dict)
    fail = chaos_entries.get('fail')
    delay = chaos_entries.get('delay')

    if fail == 'drop':
        return ['tcp-request content reject'], discovery_dict

    if fail == 'error_503':
        # No additional frontend_options, but use the
        # base (no-op) discovery method
        discovery_dict = {'method': 'base'}
        return [], discovery_dict

    if delay:
        return [
            'tcp-request inspect-delay {0}'.format(delay),
            'tcp-request content accept if WAIT_END'
        ], discovery_dict

    return [], discovery_dict


def merge_dict_for_my_grouping(chaos_dict):
    """ Given a dictionary where the top-level keys are
    groupings (ecosystem, habitat, etc), merge the subdictionaries
    whose values match the grouping that this host is in.
    e.g.

    habitat:
        sfo2:
            some_key: some_value
    runtimeenv:
        prod:
            another_key: another_value
        devc:
            foo_key: bar_value

    for a host in sfo2/prod, would return
        {'some_key': some_value, 'another_key': another_value}
    """
    result = {}
    for grouping_type, grouping_dict in chaos_dict.iteritems():
        my_grouping = get_my_grouping(grouping_type)
        entry = grouping_dict.get(my_grouping, {})
        result.update(entry)
    return result


def get_my_grouping(grouping_type):
    with open('/nail/etc/{0}'.format(grouping_type)) as fd:
        return fd.read().strip()


def main():
    my_config = get_config()

    new_synapse_config = generate_configuration(
        my_config, get_zookeeper_topology(), get_all_namespaces()
    )

    with tempfile.NamedTemporaryFile() as tmp_file:
        new_synapse_config_path = tmp_file.name
        with open(new_synapse_config_path, 'w') as fp:
            json.dump(new_synapse_config, fp, sort_keys=True, indent=4, separators=(',', ': '))

        # Match permissions that puppet expects
        os.chmod(new_synapse_config_path, 0644)

        # Restart synapse if the config files differ
        should_restart = not filecmp.cmp(new_synapse_config_path, my_config['config_file'])

        # Always swap new config file into place.  Our monitoring system
        # checks the config['config_file'] file age to ensure that it is
        # continually being updated.
        shutil.copy(new_synapse_config_path, my_config['config_file'])

        if should_restart:
            subprocess.check_call(SYNAPSE_RESTART_COMMAND)

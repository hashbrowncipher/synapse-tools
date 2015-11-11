import contextlib
import csv
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib2

import kazoo.client
import pytest


ZOOKEEPER_CONNECT_STRING = "zookeeper_1:2181"

MY_IP_ADDRESS = socket.gethostbyname(socket.gethostname())

# Authoritative data for tests
SERVICES = {
    # HTTP service with a custom endpoint
    'service_three.main': {
        'host': 'servicethree_1',
        'ip_address': socket.gethostbyname('servicethree_1'),
        'port': 1024,
        'proxy_port': 20060,
        'mode': 'http',
        'healthcheck_uri': '/my_healthcheck_endpoint',
        'discover': 'habitat'
    },

    # TCP service
    'service_one.main': {
        'host': 'serviceone_1',
        'ip_address': socket.gethostbyname('serviceone_1'),
        'port': 1025,
        'proxy_port': 20028,
        'mode': 'tcp',
        'discover': 'region'
    },

    # HTTP service with a custom endpoint and chaos
    'service_three_chaos.main': {
        'host': 'servicethreechaos_1',
        'ip_address': socket.gethostbyname('servicethreechaos_1'),
        'port': 1024,
        'proxy_port': 20061,
        'mode': 'http',
        'healthcheck_uri': '/my_healthcheck_endpoint',
        'chaos': True,
        'discover': 'region'
    },

    # HTTP with headers required for the healthcheck
    'service_two.main': {
        'host': 'servicetwo_1',
        'ip_address': socket.gethostbyname('servicetwo_1'),
        'port': 1999,
        'proxy_port': 20090,
        'mode': 'http',
        'discover': 'habitat',
        'healthcheck_uri': '/lil_brudder',
        'extra_healthcheck_headers': {
            'X-Mode': 'ro',
        }
    }
}

# How long Synapse gets to configure HAProxy on startup.  This value is
# intentionally generous to avoid any Jenkins flakes.
SETUP_DELAY_S = 60

SOCKET_TIMEOUT = 10

SYNAPSE_ROOT_DIR = '/var/run/synapse'


@pytest.yield_fixture(scope="module")
def setup():
    # Install synapse-tools
    subprocess.check_call('dpkg -i /work/dist/synapse-tools_*.deb', shell=True)

    os.makedirs(SYNAPSE_ROOT_DIR)

    zk = kazoo.client.KazooClient(hosts=ZOOKEEPER_CONNECT_STRING)
    zk.start()
    try:
        # Fake out a nerve registration in Zookeeper for each service
        for name, data in SERVICES.iteritems():
            zk.create(
                path=('/nerve/%s:my_%s/%s/itesthost' %
                      (data['discover'], data['discover'], name)),
                value=('{"host": "%s", "port": %d, "name": "%s"}' %
                       (data['ip_address'], data['port'], data['host'])),
                ephemeral=True,
                sequence=True,
                makepath=True,
            )

        # This is the tool that is installed by the synapse-tools package.
        # Run it to generate a new synapse configuration file.
        subprocess.check_call(['configure_synapse'])

        # Normally configure_synapse would start up synapse using 'service synapse start'.
        # However, this silently fails because we don't have an init process in our
        # Docker container.  So instead we manually start up synapse ourselves.
        synapse_process = subprocess.Popen(
            'synapse --config /etc/synapse/synapse.conf.json'.split(),
            env={"PATH": "/opt/rbenv/bin:" + os.environ['PATH']})

        time.sleep(SETUP_DELAY_S)

        try:
            yield
        finally:
            synapse_process.kill()
            synapse_process.wait()
    finally:
        zk.stop()


def test_haproxy_synapse_reaper(setup):
    # This should run with no errors.  Everything is running as root, so we need
    # to use the --username option here.
    subprocess.check_call(['haproxy_synapse_reaper', '--username', 'root'])


def test_synapse_qdisc_tool(setup):
    # Can't actually manipulate qdisc or iptables in a docker, so this
    # is what we have for now
    subprocess.check_call(['synapse_qdisc_tool', '--help'])


def test_synapse_services(setup):
    expected_services = ['service_three.main', 'service_one.main', 'service_three_chaos.main', 'service_two.main']

    with open('/etc/synapse/synapse.conf.json') as fd:
        synapse_config = json.load(fd)
    actual_services = synapse_config['services'].keys()

    assert set(expected_services) == set(actual_services)


def test_http_synapse_service_config(setup):
    expected_service_entry = {
        'default_servers': [],
        'use_previous_backends': False,
        'discovery': {
            'hosts': [ZOOKEEPER_CONNECT_STRING],
            'method': 'zookeeper',
            'path': '/nerve/habitat:my_habitat/service_three.main'},
        'haproxy': {
            'listen': [
                'option httpchk GET /http/service_three.main/0/my_healthcheck_endpoint',
                'http-check send-state',
                'retries 2',
                'timeout connect 10000ms',
                'timeout server 11000ms'
            ],
            'frontend': [
                'timeout client 11000ms',
                'capture request header X-B3-SpanId len 64',
                'capture request header X-B3-TraceId len 64',
                'capture request header X-B3-ParentSpanId len 64',
                'capture request header X-B3-Flags len 10',
                'capture request header X-B3-Sampled len 10',
                'option httplog',
            ],
            'backend': [
            ],
            'port': '20060',
            'server_options': 'check port 6666 observe layer7'
        }
    }

    with open('/etc/synapse/synapse.conf.json') as fd:
        synapse_config = json.load(fd)

    actual_service_entry = synapse_config['services'].get('service_three.main')
    assert expected_service_entry == actual_service_entry


def test_tcp_synapse_service_config(setup):
    expected_service_entry = {
        'default_servers': [],
        'use_previous_backends': False,
        'discovery': {
            'hosts': [ZOOKEEPER_CONNECT_STRING],
            'method': 'zookeeper',
            'path': '/nerve/region:my_region/service_one.main'},
        'haproxy': {
            'listen': [
                'option httpchk GET /tcp/service_one.main/0/status',
                'http-check send-state',
                'mode tcp',
                'timeout connect 10000ms',
                'timeout server 11000ms'
            ],
            'frontend': [
                'timeout client 12000ms',
                'option tcplog',
            ],
            'backend': [
            ],
            'port': '20028',
            'server_options': 'check port 6666 observe layer4'
        }
    }

    with open('/etc/synapse/synapse.conf.json') as fd:
        synapse_config = json.load(fd)
    actual_service_entry = synapse_config['services'].get('service_one.main')

    assert expected_service_entry == actual_service_entry

def test_hacheck(setup):
    for name, data in SERVICES.iteritems():
        # Just test our HTTP service
        if data['mode'] != 'http':
            continue

        url = 'http://%s:6666/http/%s/0%s' % (
            data['ip_address'], name, data['healthcheck_uri'])

        headers = {
            'X-Haproxy-Server-State':
                'UP 2/3; host=srv2; port=%d; name=bck/srv2;'
                'node=lb1; weight=1/2; scur=13/22; qcur=0' % data['port']
        }
        headers.update(data.get('extra_healthcheck_headers', {}))

        request = urllib2.Request(url=url, headers=headers)

        with contextlib.closing(
                urllib2.urlopen(request, timeout=SOCKET_TIMEOUT)) as page:
            assert page.read().strip() == 'OK'


def test_synapse_haproxy_stats_page(setup):
    haproxy_stats_uri = 'http://localhost:3212/;csv'

    with contextlib.closing(
            urllib2.urlopen(haproxy_stats_uri, timeout=SOCKET_TIMEOUT)) as haproxy_stats:
        reader = csv.DictReader(haproxy_stats)
        rows = [(row['# pxname'], row['svname'], row['check_status']) for row in reader]

        for name, data in SERVICES.iteritems():
            if 'chaos' in data:
                continue

            svname = '%s:%d_%s' % (data['ip_address'], data['port'], data['host'])
            check_status = 'L7OK'
            assert (name, svname, check_status) in rows


def test_http_service_is_accessible_using_haproxy(setup):
    for name, data in SERVICES.iteritems():
        if data['mode'] == 'http' and 'chaos' not in data:
            uri = 'http://localhost:%d%s' % (data['proxy_port'], data['healthcheck_uri'])
            with contextlib.closing(urllib2.urlopen(uri, timeout=SOCKET_TIMEOUT)) as page:
                assert page.read().strip() == 'OK'


def test_tcp_service_is_accessible_using_haproxy(setup):
    for name, data in SERVICES.iteritems():
        if data['mode'] == 'tcp':
            s = socket.create_connection(
                address=(data['ip_address'], data['port']),
                timeout=SOCKET_TIMEOUT)
            s.close()


def test_file_output(setup):
    output_directory = os.path.join(SYNAPSE_ROOT_DIR, 'services')
    for name, data in SERVICES.iteritems():
        with open(os.path.join(output_directory, name + '.json')) as f:
            service_data = json.load(f)
            if 'chaos' in data:
                assert len(service_data) == 0
                continue

            assert len(service_data) == 1

            service_instance = service_data[0]
            assert service_instance['name'] == data['host']
            assert service_instance['port'] == data['port']
            assert service_instance['host'] == data['ip_address']


def test_http_service_returns_503(setup):
    data = SERVICES['service_three_chaos.main']
    uri = 'http://localhost:%d%s' % (data['proxy_port'], data['healthcheck_uri'])
    with pytest.raises(urllib2.HTTPError) as excinfo:
        with contextlib.closing(urllib2.urlopen(uri, timeout=SOCKET_TIMEOUT)):
            assert False
        assert excinfo.value.getcode() == 503


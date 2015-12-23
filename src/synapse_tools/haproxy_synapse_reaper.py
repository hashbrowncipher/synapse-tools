#!/usr/bin/env python

"""When haproxy is soft restarted, the previous 'main' haproxy instance sticks
around as an 'alumnus' until all connections have drained:

   main --[soft restart]--> alumnus --[connections drained]--> <dead>

If the alumus is handling long-lived connections (e.g. scribe), it could take
a long time to exit.  This script bounds the length of time that a haproxy
instance can spend in the alumnus state by killing such processes after a
specified period of time.

In the haproxy-synapse startup script, after the new haproxy has started up, it
records the PID of alumnus haproxy by touching a file in state_dir named after
the PID.  When that file reaches a certain age (measured by mtime), it becomes
eligible for reaping by this script.

See SRV-1404 for more background info.
"""


from bisect import bisect
import errno
import logging
import os
import time

import argparse
import psutil


DEFAULT_STATE_DIR = '/var/run/synapse/alumni'

DEFAULT_REAP_AGE_S = 60 * 60

DEFAULT_MAX_PROCS = 10

LOG_FORMAT = '%(levelname)s %(message)s'

log = logging.getLogger()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--state-dir', default=DEFAULT_STATE_DIR,
                        help='State directory (default: %(default)s).')
    parser.add_argument('-r', '--reap-age', type=int, default=DEFAULT_REAP_AGE_S,
                        help='Reap age (default: %(default)s).')
    parser.add_argument('-p', '--max-procs', type=int, default=DEFAULT_MAX_PROCS,
                        help='Maximum processes to leave alive (default: %(default)s).')
    return parser.parse_args()


def get_death_warrants(state_dir, reap_age, max_procs):
    now = time.time()
    reap_count = 0

    age = lambda x: now - os.path.getmtime(os.path.join(state_dir, x))
    pidfile_ages = sorted((age(i), int(i)) for i in os.listdir(state_dir))

    # Don't traverse beyond the end of the list
    hi = min(len(pidfile_ages), max_procs)
    cut = bisect(pidfile_ages, (reap_age, ''), hi=hi)

    # If you are in pidfile_ages[0:cut], congrats: you made the cut.
    # If you are in pidfile_ages[cut:], you're getting killed. Apologies.

    return [(pid, age, cut + index) for index, (age, pid) in
            enumerate(pidfile_ages[cut:])]


def execute_alumni(state_dir, death_warrants):
    reap_count = 0

    for pid, age, index in death_warrants:
        try:
            proc = psutil.Process(pid)
            if proc.name() == 'haproxy-synapse':
                log.info('Reaping process %d with age %ds and index %d' %
                         (pid, age, index))

                proc.kill()
                reap_count += 1
        except psutil.NoSuchProcess:
            log.warn('Process %d has disappeared' % pid)

        os.remove(os.path.join(state_dir, str(pid)))

    return reap_count


def ensure_path_exists(path):
    try:
        os.mkdir(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise


def main():
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)

    args = parse_args()
    ensure_path_exists(args.state_dir)
    warrants = get_death_warrants(args.state_dir, args.reap_age, args.max_procs)
    reap_count = execute_alumni(args.state_dir, warrants)

    log.info('Reaped %d processes' % reap_count)


if __name__ == '__main__':
    main()

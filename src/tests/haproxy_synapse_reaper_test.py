import mock

from synapse_tools import haproxy_synapse_reaper


def test_parse_args():
    mock_argv = ['haproxy_synapse_reaper']
    with mock.patch('sys.argv', mock_argv):
            args = haproxy_synapse_reaper.parse_args()

    assert args.state_dir == '/var/run/synapse/alumni'
    assert args.reap_age == 3600


def test_parse_args_state_dir():
    mock_argv = ['haproxy_synapse_reaper', '--state-dir', 'foo']
    with mock.patch('sys.argv', mock_argv):
            args = haproxy_synapse_reaper.parse_args()

    assert args.state_dir == 'foo'


def test_parse_args_reap_age():
    mock_argv = ['haproxy_synapse_reaper', '--reap-age', '42']
    with mock.patch('sys.argv', mock_argv):
            args = haproxy_synapse_reaper.parse_args()

    assert args.reap_age == 42


@mock.patch('synapse_tools.haproxy_synapse_reaper.time.time')
@mock.patch('synapse_tools.haproxy_synapse_reaper.os.path.getmtime')
@mock.patch('synapse_tools.haproxy_synapse_reaper.os.listdir')
def test_kill_alumni_if_too_old(mock_listdir, mock_getmtime, mock_time):
    mock_time.return_value = 3601
    mock_listdir.return_value = ['45', '43', '42']
    mock_getmtime.side_effect = [1, 0, 1]

    warrants = haproxy_synapse_reaper.get_death_warrants(
        state_dir='/state_dir', reap_age=3600, max_procs=10)

    assert warrants == [(43, 3601, 2)]


@mock.patch('synapse_tools.haproxy_synapse_reaper.time.time')
@mock.patch('synapse_tools.haproxy_synapse_reaper.os.path.getmtime')
@mock.patch('synapse_tools.haproxy_synapse_reaper.os.listdir')
def test_kill_alumni_if_too_many(mock_listdir, mock_getmtime, mock_time):
    mock_time.return_value = 5
    mock_listdir.return_value = ['45', '43', '42', '41']
    mock_getmtime.side_effect = [4, 3, 2, 1]

    warrants = haproxy_synapse_reaper.get_death_warrants(
        state_dir='/state_dir', reap_age=3600, max_procs=1)

    assert warrants == [
        (43, 2, 1),
        (42, 3, 2),
        (41, 4, 3),
    ]



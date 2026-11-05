# Stage 1: robot-side iperf3 client

This patch adds the robot execution layer only. Existing NMS scheduling still
runs its original local client. Do not register an Internet experiment through
the unchanged NMS expecting this patch to redirect it.

Changed: config.py, robot_dispatch.py. Added: robot_handlers_traffic.py,
tests/test_robot_traffic.py, STAGE1_TRAFFIC.md. Apply in the robot code root.

    git apply --check robot_iperf3_client_stage1.patch
    git apply robot_iperf3_client_stage1.patch
    python3 -m unittest discover -s tests -p test_robot_traffic.py -v

The unit tests use isolated temporary storage and real local child processes,
with network discovery mocked. They send no network traffic. They cover script
direction translation, AX210 source binding, nonblocking execution, duplicate
start suppression, stop isolation, result acknowledgement, invalid input,
launch failure, JSON-reported errors, and nonzero exits. No physical robot or
Internet iperf3 server was available for this verification.

## Manual short test, after a server is ready

Use an authorized Internet server with iperf3 listening on TCP and UDP port 5201.
The following Python can run on the robot using the same account as its agent.
It starts an upload (reverse=True retains the existing script meaning).
Set HOST to the real server address. No endpoint is hardcoded in the patch.

    import time
    from robot_handlers_traffic import start_session, pending_records
    HOST = 'REPLACE_WITH_SERVER_ADDRESS'
    args = dict(session_id='SStage1', server_host=HOST, server_port=5201,
                duration_sec=180, report_interval_sec=60, reverse=True,
                protocol='udp', ac='bk', bitrate='5M', packet_size=1200)
    ok, detail = start_session('twin-scout-alpha', args, 'manual-stage1-upload-001')
    print(ok, detail)
    if ok:
        while not any(r['kind'] == 'result' and
                      r['execution_id'] == 'manual-stage1-upload-001'
                      for r in pending_records(limit=200)):
            time.sleep(1)
        print(pending_records(limit=200))

Use a fresh execution ID for a new test. For download, use reverse=False and a
new execution ID. To stop from the same Python process:

    from robot_handlers_traffic import stop_session
    print(stop_session('twin-scout-alpha', {'session_id': 'SStage1'}))

The execution manager currently belongs to its Python process: a separate
Python invocation cannot stop a session started by another invocation. NMS
integration will invoke it within the robot agent process. Keep that process
alive until the result is saved. A start ACK means the process launched; inspect
the result status to determine whether the test succeeded.

## Contract for the next patch

The NMS supplies server_host/server_port as internal delivery arguments, plus a
stable cmd_id as execution_id. These are not new spreadsheet script fields.
The handler saves event/result records in config.TRAFFIC_STATE_DIR and exposes
pending_records()/acknowledge_records(). The NMS must acknowledge individual
records only after durable acceptance and deduplicate retransmitted IDs.

Script reverse=True means robot upload (no iperf3 -R); reverse=False means
robot download (iperf3 -R). Raw JSON is preserved, and iperf_reverse records the
actual iperf3 flag. Do not treat its client-side roles as the old NMS roles.

AX210 must be connected and have an IPv4 address. The client binds that source
address. Verify the Internet route on the robot and check the AP station MAC;
source binding alone does not configure Linux routing policy. --tos markings
are retained but do not guarantee reported application-category labels.

## Remaining integration work

Robot poll delivery, NMS persistence/acknowledgements, shared server-port
allocation, experiment cancellation, restart recovery for active processes,
and downstream direction interpretation belong to the next patches. Completed
pending files survive restart, but active-process recovery is not implemented
here. Do not restart the agent during this first-stage test. Historical
execution markers suppress repeats and currently require deliberate retention
management in the later integration. Existing robot server services remain.

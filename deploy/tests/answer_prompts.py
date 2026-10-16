"""Run a command on a terminal and answer its prompts (deploy/tests: confirmation flows).

	python3 answer_prompts.py <answers file> <log file> <command...>

Each time the output ends in a prompt (": "), the next line of the answers file is typed.
The whole terminal output goes to the log file; the exit status is the command's.
"""

import os
import pty
import select
import subprocess
import sys

answers = open(sys.argv[1]).read().splitlines()
master, slave = pty.openpty()
process = subprocess.Popen(sys.argv[3:], stdin=slave, stdout=slave, stderr=slave, close_fds=True)
os.close(slave)
pending, output = b"", []
while True:
	ready, _, _ = select.select([master], [], [], 120)
	if not ready:
		break
	try:
		data = os.read(master, 4096)
	except OSError:
		break
	if not data:
		break
	output.append(data)
	pending += data
	if pending.rstrip(b" ").endswith(b":") and answers:
		os.write(master, (answers.pop(0) + "\n").encode())
		pending = b""
process.wait()
with open(sys.argv[2], "w") as log:
	log.write(b"".join(output).decode(errors="replace").replace("\r", ""))
sys.exit(process.returncode)

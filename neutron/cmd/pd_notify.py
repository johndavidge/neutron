#!/usr/bin/python

import os
import signal
import sys
import tempfile


def replace_file(file_name, data):
    """Replaces the contents of file_name with data in a safe manner.

    First write to a temp file and then rename. Since POSIX renames are
    atomic, the file is unlikely to be corrupted by competing writes.

    We create the tempfile on the same device to ensure that it can be renamed.
    """

    base_dir = os.path.dirname(os.path.abspath(file_name))
    tmp_file = tempfile.NamedTemporaryFile('w+', dir=base_dir, delete=False)
    tmp_file.write(data)
    tmp_file.close()
    os.chmod(tmp_file.name, 0o644)
    os.rename(tmp_file.name, file_name)


def main():
    operation = sys.argv[1]
    prefix_fname = sys.argv[2]
    agent_pid = sys.argv[3]
    prefix = os.environ.get('PREFIX1')

    if operation == "add" or operation == "update":
        replace_file(prefix_fname, "%s/64" % prefix)
    elif operation == "delete":
        replace_file(prefix_fname, "::/64")
    os.kill(int(agent_pid), signal.SIGHUP)


if __name__ == "__main__":
    main()

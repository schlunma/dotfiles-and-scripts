#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Easy SSH synchronization
# Copyright (c) 2024 Manuel Schlund <schlunma@gmail.com>
# Licensed under the GNU General Public License v3.0 (or later).

"""Easy synchronization between different hosts using SSH.

This script performs synchronization operations according to a given
configuration file (default location: ~/.sync.yml).

Example
-------
Start synchronization to host <host>::

    $ python sync.py <host>

All options can be found by calling::

    $ python sync.py --help

Notes
-----
The configuration file is given in YAML format, e.g.::

    ALIASES:
      alias_for_host1: host1

    host1:
      file1: path/to/file1
      file2: path/to/file2

    host2:
      _PATH: ~/path/to/somewhere/
      file2: another/path/to/file2

    host3:
      file1: yet/another/path/to/file1

All top-level entries (except for `ALIASES`) are hostnames of the different
machines. If no `_PATH` option is given, the particular hostname has to be
defined in the SSH configuration file (~/.ssh/config). The paths of all files
or directories have to be given relative to the home directory of the
particular machine or, if given, relative to the `_PATH` option.

The `ALIASES` section offers the possibility to assign aliases for the
different hosts.

"""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
import time
from argparse import ArgumentParser
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml

if TYPE_CHECKING:
    from argparse import Namespace


class Sync():
    """Synchronization class."""

    _ALIASES = 'ALIASES'
    _COPYRIGHT = 'Copyright (c) 2023 Manuel Schlund <schlunma@gmail.com>'
    _DEFAULT_CONFIGFILE = '~/.sync.yml'
    _DEFAULT_EXCLUDE = '--exclude="*.swp" '
    _DEFAULT_LOGFILE = '~/.sync.log'
    _DEFAULT_NTASKS = 6
    _DELIMITER = 50 * '-'
    _LOG_FORMATTER = logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s'
    )
    _NAME = 'Easy SSH synchronization'
    _PATH = '_PATH'
    _PRE_COMMAND = (
        'if command -v "checkssh" &>/dev/null; then\ncheckssh\nfi'
    )
    _SYNC_COMMAND = 'rsync -auP'

    def __init__(self) -> None:
        """Initialize class instance."""
        self.parser: ArgumentParser = self._setup_parser()
        self.args: Namespace = self._parse_args()

        self.logger: logging.Logger = self._setup_logger()

        self.config: dict = self._read_config()

        (self.this_host, self.target_hosts) = self._get_hosts()
        self.sync_command: str = self._get_sync_command()

        self.semaphores = self._get_semaphores()

    def _get_hosts(self) -> tuple[str, list[str]]:
        """Get correct host names (according to the configuration file)."""
        all_hosts = [host for host in self.config if host != self._ALIASES]
        this_host_fullname = socket.gethostname()
        this_host = None
        other_hosts = []
        for host in all_hosts:
            if host in this_host_fullname:
                this_host = host
            else:
                other_hosts.append(host)

        # Process input
        target_hosts = []
        if 'all' in self.args.targets:
            target_hosts = other_hosts
        else:
            for target_host in self.args.targets:
                for host in all_hosts:
                    if host in target_host:
                        target_hosts.append(host)
                        break
                else:
                    logging.warning(
                        "Could not find host '%s' in configuration file '%s'",
                        target_host,
                        self.args.configfile,
                    )

        # Catch invalid input
        if this_host is None:
            logging.error(
                "Could not find current machine '%s' in configuration file "
                "'%s'",
                this_host_fullname,
                self.args.configfile,
            )
            exit(1)
        if not target_hosts:
            logging.error(
                "Could not find any valid host for %s in configuration file "
                "'%s'",
                self.args.targets,
                self.args.configfile,
            )
            exit(1)
        if this_host in target_hosts:
            logging.error("Cannot sync host '%s' with itself", this_host)
            exit(1)

        logging.debug(
            "Found hosts: current machine: '%s', target host(s): %s",
            this_host,
            target_hosts,
        )
        return (this_host, target_hosts)

    def _get_log_info(self, stdout: str, src: str, dest: str) -> list[str]:
        """Get info log messages for a single synchronization process."""
        info_list = []
        for info in stdout.split('\n')[1:]:
            # Not relevant information
            not_relevant = any([
                '\r' in info,
                info == '',
                info == './',
                info.startswith('sent '),
                info.startswith('total size is'),
            ])
            if not_relevant:
                pass

            # New directory created
            elif 'created directory' in info:
                if self.args.dry_run:
                    info = info.replace(
                        "created directory ", "Would create directory '"
                    ) + "'"
                else:
                    info = info.replace(
                        "created directory ", "Created directory '"
                    ) + "'"
                info_list.append(f'    {info}')

            # File deleted
            elif info.startswith('deleting'):
                info = info.replace('deleting ', '', 1)
                d_str = dest + info if dest.endswith('/') else dest
                if self.args.dry_run:
                    prefix = "    Would delete "
                else:
                    prefix = "    Deleted "
                info_list.append(f"{prefix}'{d_str}'")

            # File moved
            else:
                s_str = src + info if src.endswith('/') else src
                d_str = dest + info if dest.endswith('/') else dest
                if self.args.dry_run:
                    prefix = "    Would move "
                else:
                    prefix = "    Successfully moved "
                info_list.append(f"{prefix}'{s_str}' to '{d_str}'")

        return list(set(info_list))

    async def _get_one_sync_task(
        self, element: str, direction: Literal['up', 'down'], target_host: str,
    ) -> tuple[str, str, str, str]:
        """Get one synchronization task."""
        # Get target host path
        if self._PATH in self.config[target_host]:
            target_host_path = self._expanduser(
                self.config[target_host][self._PATH]
            )
        else:
            target_host_path = f'{target_host}:./'

        # Get elements
        this_element = self._expanduser(
            f'~/{self.config[self.this_host][element]}'
        )
        target_element = target_host_path + self.config[target_host][element]

        # Get synchronization command
        if direction == 'up':
            src = this_element
            dest = target_element
        else:
            src = target_element
            dest = this_element
        command_str = f'{self.sync_command} {src} {dest}'

        # Initialize synchronization task
        logging.debug("    Initializing command '%s'", command_str)

        # Limit number of concurrent tasks
        # see https://docs.python.org/3/library/asyncio-sync.html#semaphore
        async with self.semaphores[target_host][direction]:
            process = await asyncio.create_subprocess_shell(
                command_str, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            out = await process.communicate()

        result = (src, dest, out[0].decode('utf-8'), out[1].decode('utf-8'))
        return result

    def _get_semaphores(self) -> dict:
        """Get semaphore for each event loop."""
        semaphores = {
            host: {
                'up': asyncio.Semaphore(self.args.ntasks),
                'down': asyncio.Semaphore(self.args.ntasks),
            } for host in self.target_hosts
        }
        return semaphores

    def _get_sync_command(self) -> str:
        """Get synchronization command."""
        exclude = self._DEFAULT_EXCLUDE
        try:
            for exc in self.args.exclude:
                exclude += '--exclude="' + exc + '" '
        except TypeError:
            pass
        sync_command = self._SYNC_COMMAND + ' ' + exclude.strip()

        if self.args.dry_run:
            sync_command += ' -n'

        if self.args.delete:
            sync_command += ' --delete'

        return sync_command

    def _parse_args(self) -> Namespace:
        """Parse command line arguments."""
        args = self.parser.parse_args()
        args.configfile = Path(args.configfile).expanduser()
        args.logfile = Path(args.logfile).expanduser()

        return args

    def _print_welcome(self) -> None:
        """Print welcome message."""
        logging.info(self._DELIMITER)
        logging.info(self._NAME)
        logging.info(self._COPYRIGHT)
        logging.info(
            "This program comes with ABSOLUTELY NO WARRANTY; for details "
            "type `python sync.py --help`"
        )
        logging.info(
            "This is free software, and you are welcome to redistribute it "
            "under certain conditions (see GNU General Public License, "
            "Version 3 or later)"
        )
        logging.info(self._DELIMITER)
        if self.args.dry_run:
            dry_run_str = "simulation of "
        else:
            dry_run_str = ""
        logging.debug("Starting %ssynchronization", dry_run_str)

        # Warn if user selected --delete option
        if self.args.delete:
            logging.warning("--delete option may lead to loss of data")
            time.sleep(2.0)

    async def _process_host(
        self, target_host: str, direction: Literal['up', 'down']
    ) -> bool:
        """Perform concurrent one-way synchronization to one host."""
        # Get direction
        prefix = 'Upload' if direction == 'up' else 'Download'

        # Get elements
        elements_this = list(self.config[self.this_host])
        elements_target = list(self.config[target_host])

        # Check if the hosts share elements
        if set(elements_this).isdisjoint(elements_target):
            logging.info(
                "    %s: the two hosts do not share common elements", prefix
            )
            return True

        # Iterate through elements and create a process for each
        # synchronization task
        successful = True
        tasks = []
        for element in elements_this:
            if element == self._PATH:
                continue
            if element in elements_target:
                tasks.append(
                    self._get_one_sync_task(element, direction, target_host)
                )

        # Run synchronization tasks concurrently
        results = await asyncio.gather(*tasks)

        # Log sync operations
        for result in results:
            (src, dest, stdout, stderr) = result
            info_list = self._get_log_info(stdout, src, dest)

            # Info
            if stdout:
                logging.debug(f"    {stdout}")
            if info_list:
                for info in info_list:
                    logging.info(info)

            # Errors
            connection_error = any([
                'Connection refused' in stderr,
                'Could not resolve hostname' in stderr,
            ])
            if connection_error:
                logging.error(
                    "    %s: cannot connect to host '%s'", prefix, target_host
                )
                return False
            if stderr:
                logging.warning(
                    "    %s", stderr.strip('\n').replace('\n', '; ')
                )
                successful = False

        return successful

    def _read_config(self) -> dict:
        """Read configuration file."""
        if not self.args.configfile.is_file():
            logging.error(
                "Configuration file '%s' does not exist", self.args.configfile)
            exit(1)

        with open(self.args.configfile, 'r') as in_file:
            config = yaml.safe_load(in_file)
        logging.debug(
            "Successfully read configuration file '%s'", self.args.configfile
        )

        # Process possible aliases
        if self._ALIASES in config:
            aliases = config[self._ALIASES]
            for (i, old_host) in enumerate(self.args.targets):
                if old_host in aliases:
                    new_host = aliases[old_host]
                    self.args.targets[i] = new_host
                    logging.info("Aliased '%s' to '%s'", old_host, new_host)

        return config

    def _run_sync(self) -> None:
        """Run entire synchronization process."""
        # Perform pre-command
        logging.debug(
            "Performing pre-command: '%s'",
            self._PRE_COMMAND.replace('\n', ' '),
        )
        self.logger.info(
            "Running at most %d concurrent task(s)", self.args.ntasks
        )
        pre_comm = subprocess.Popen(
            self._PRE_COMMAND,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        (out, err) = pre_comm.communicate()
        stdout = out.decode('utf-8')
        stderr = err.decode('utf-8')
        if stdout:
            logging.info(stdout.strip('\n'))
        if stderr:
            logging.error(stderr.strip('\n'))
        logging.info("")

        # Get synchronization direction
        # Note: If no option is given, also perform both directions
        if self.args.up == self.args.down:
            perform_up = True
            perform_down = True
        else:
            perform_up = self.args.up
            perform_down = self.args.down

        # Loop over all target hosts
        if self.args.dry_run:
            dry_run_str = "simulation of "
        else:
            dry_run_str = ""
        for target_host in self.target_hosts:
            logging.debug(
                "Started %ssynchronization between '%s' and '%s'",
                dry_run_str,
                self.this_host,
                target_host,
            )

            # Sync
            success_up = True
            success_down = True
            if perform_up:
                logging.info(
                    "Started %supload '%s' --> '%s'",
                    dry_run_str,
                    self.this_host,
                    target_host,
                )
                success_up = asyncio.run(self._process_host(target_host, 'up'))
            if perform_down:
                logging.info(
                    "Started %sdownload '%s' --> '%s'",
                    dry_run_str,
                    target_host,
                    self.this_host,
                )
                success_down = asyncio.run(
                    self._process_host(target_host, 'down')
                )
            successful = (success_up and success_down)

            # Log
            prefix = "Simulated" if self.args.dry_run else "Completed"
            if successful:
                logging.info(
                    "%s synchronization between '%s' and '%s'",
                    prefix,
                    self.this_host,
                    target_host,
                )
            else:
                logging.warning(
                    "%s synchronization between '%s' and '%s' with error(s)",
                    prefix,
                    self.this_host,
                    target_host,
                )
            logging.info("")

        logging.debug("Finished synchronization script")
        logging.info("%s\n", self._DELIMITER)

    def _setup_logger(self) -> logging.Logger:
        """Setup the logging functionality."""
        logger = logging.getLogger()

        if self.args.verbose:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)

        # Add NullHandler (required when no output is desired)
        null_handler = logging.NullHandler()
        logger.addHandler(null_handler)

        # Real handlers
        if not self.args.no_logfile:
            file_log_handler = logging.FileHandler(self.args.logfile, mode='a')
            file_log_handler.setFormatter(self._LOG_FORMATTER)
            logger.addHandler(file_log_handler)
        if not self.args.quiet:
            console_log_handler = logging.StreamHandler()
            console_log_handler.setFormatter(self._LOG_FORMATTER)
            logger.addHandler(console_log_handler)

        return logger

    def _setup_parser(self) -> ArgumentParser:
        """Setup argument parser."""
        parser = ArgumentParser(description=f'{self._NAME}, {self._COPYRIGHT}')
        parser.add_argument(
            'targets',
            type=str,
            nargs='+',
            help="The target machines, 'all': perform all possible operations"
        )
        parser.add_argument(
            '-f',
            '--configfile',
            type=str,
            default=self._DEFAULT_CONFIGFILE,
            help="Specify synchronization configuration file",
        )
        parser.add_argument(
            '-l',
            '--logfile',
            type=str,
            default=self._DEFAULT_LOGFILE,
            help="Specify synchronization log file",
        )
        parser.add_argument(
            '-e', '--exclude', action='append', help="Exclude certain files"
        )
        parser.add_argument(
            '-d', '--down', action='store_true', help="Only download files"
        )
        parser.add_argument(
            '-u', '--up', action='store_true', help="Only upload files"
        )
        parser.add_argument(
            '-n', '--dry-run', action='store_true', help="Simulate the run"
        )
        parser.add_argument(
            '-q',
            '--quiet',
            action='store_true',
            help="Suppress output to stdout",
        )
        parser.add_argument(
            '-Q',
            '--no-logfile',
            action='store_true',
            help="Suprress output to logfile",
        )
        parser.add_argument(
            '-v', '--verbose', action='store_true', help="Show debug output"
        )
        parser.add_argument(
            '--delete',
            action='store_true',
            help="Delete non existing files on other host - use with care!",
        )
        parser.add_argument(
            '-N',
            '--ntasks',
            type=int,
            default=self._DEFAULT_NTASKS,
            help="Maximum number of concurrently run synchronization tasks",
        )

        return parser

    @staticmethod
    def _expanduser(path: str) -> str:
        """Make sure that trailing '/' are handled correctly."""
        if path.endswith('/'):
            suffix = '/'
        else:
            suffix = ''
        path = str(Path(path).expanduser())
        return f'{path}{suffix}'

    def print_help(self) -> None:
        """Print help messages."""
        self.parser.print_help()

    def run(self) -> None:
        """Start synchronization procedure."""
        self._print_welcome()
        self._run_sync()


# Call script directly
if __name__ == '__main__':
    sync_instance = Sync()
    sync_instance.run()

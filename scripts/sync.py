#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Easy SSH synchronization
# Copyright (c) 2022 Manuel Schlund <schlunma@gmail.com>
# Licensed under the GNU General Public License v3.0 (or later).


"""Easy synchronization between different hosts using SSH.

This script performs synchronization operations according to a given
configuration file (default location: ~/.sync.conf).

Example
-------
Start synchronization to host <host>::

    $ python sync.py <host>

All options can be found by calling::

    $ python sync.py --help

Notes
-----
The configuration file should have the ini format, e.g.::

    [ALIASES]
    myhost = host1

    [host1]
    file1 = path/to/file1
    file2 = path/to/file2

    [host2]
    _PATH = ~/path/to/somewhere/
    file2 = another/path/to/file2

    [host3]
    file1 = yet/another/path/to/file1

All listed (non-default) sections are hostnames of the different machines.
If no "_PATH" option is given the particular hostname has to be defined in
the ssh configuration file (~/.ssh/config). The paths of all files or
directories have to be given relative to the home directory of the particular
machine or, if given, relative to the _PATH option.

The "ALIASES" section offers the possibility to asign aliases for the
different hosts.

"""


import argparse
import configparser
import logging
import os
import socket
import subprocess
import time


class Sync(object):
    """Synchronization class."""

    # Static member variables
    _CONFIGFILE = '~/.sync.conf'
    _LOGFILE = '~/.sync.log'
    _PRE_COMMAND = 'if command -v "checkssh" &>/dev/null; then \ncheckssh' + \
                   '\nfi'
    _SYNC_COMMAND = 'rsync -auP'
    _LOG_FORMATTER = logging.Formatter('%(asctime)s %(levelname)s: ' +
                                       '%(message)s')
    _EXCLUDE = '--exclude="*.swp" '
    _PATH = '_PATH'
    _ALIASES = 'ALIASES'
    _NAME = 'Easy SSH synchronization'
    _COPYRIGHT = 'Copyright (c) 2021 Manuel Schlund <schlunma@gmail.com>'
    _DELIMITER = 50 * '-'

    def __init__(self):
        """Initialize member variables."""
        self.args = None
        self.config = configparser.ConfigParser()
        self.parser = argparse.ArgumentParser(
            description=Sync._NAME + ', ' + Sync._COPYRIGHT)
        self.this_host = None
        self.logger = logging.getLogger()
        self.sync_command = ''
        self.target_hosts = []

    def _get_hosts(self):
        """Get correct host names (according to the configuration file)."""
        all_hosts = [elem for elem in self.config.sections() if
                     elem != Sync._ALIASES]
        other_hosts = []
        myhost = socket.gethostname()
        for host in all_hosts:
            if host in myhost:
                self.this_host = host
            else:
                other_hosts.append(host)

        # Process input
        if 'all' in self.args.targets:
            self.target_hosts = other_hosts
        else:
            for target_host in self.args.targets:
                found_host = False
                for host in all_hosts:
                    if host in target_host:
                        self.target_hosts.append(host)
                        found_host = True
                        break
                if not found_host:
                    logging.warning("Could not find host '%s' in "
                                    "configuration file '%s'", target_host,
                                    self.args.configfile)

        # Catch invalid input
        if self.this_host is None:
            logging.error("Could not find current machine '%s' in "
                          "configuration file '%s'", myhost,
                          self.args.configfile)
            exit(1)
        if not self.target_hosts:
            logging.error("Could not find any valid host for '%s' in "
                          "configuration file '%s'", self.args.targets,
                          self.args.configfile)
            exit(1)
        if self.this_host in self.target_hosts:
            logging.error("Cannot sync host '%s' with itself", self.this_host)
            exit(1)
        logging.debug("Found hosts: current machine: '%s', target host(s): %s",
                      self.this_host, self.target_hosts)

    def _parse_args(self):
        """Parse command line arguments."""
        self.parser.add_argument(
            'targets', type=str, nargs='+',
            help="The target machines, 'all': perform all possible operations")
        self.parser.add_argument(
            '-f', '--configfile', type=str, default=Sync._CONFIGFILE,
            help="Specify synchronization configuration file")
        self.parser.add_argument(
            '-l', '--logfile', type=str, default=Sync._LOGFILE,
            help="Specify synchronization log file")
        self.parser.add_argument('-e', '--exclude', action='append',
                                 help="Exclude certain files")
        self.parser.add_argument('-d', '--down', action='store_true',
                                 help="Only download files")
        self.parser.add_argument('-u', '--up', action='store_true',
                                 help="Only upload files")
        self.parser.add_argument('-n', '--dry-run', action='store_true',
                                 help="Simulate the run")
        self.parser.add_argument('-q', '--quiet', action='store_true',
                                 help="Suppress output to stdout")
        self.parser.add_argument('-Q', '--no-logfile', action='store_true',
                                 help="Suprress output to logfile")
        self.parser.add_argument('-v', '--verbose', action='store_true',
                                 help="Show debug output")
        self.parser.add_argument(
            '--delete', action='store_true',
            help="Delete non existing files on other host - use with care!")

        # Read arguments
        self.args = self.parser.parse_args()
        self.args.configfile = os.path.expanduser(self.args.configfile)
        self.args.logfile = os.path.expanduser(self.args.logfile)
        exclude = Sync._EXCLUDE
        try:
            for exc in self.args.exclude:
                exclude += '--exclude="' + exc + '" '
        except TypeError:
            pass
        self.sync_command = Sync._SYNC_COMMAND + ' ' + exclude.strip()
        if self.args.dry_run:
            self.sync_command += ' -n'
        if self.args.delete:
            self.sync_command += ' --delete'

    def _print_welcome(self):
        """Print welcome message."""
        logging.info(Sync._DELIMITER)
        logging.info(Sync._NAME)
        logging.info(Sync._COPYRIGHT)
        logging.info("This program comes with ABSOLUTELY NO WARRANTY; for "
                     "details type `python sync.py --help`")
        logging.info("This is free software, and you are welcome to "
                     "redistribute it under certain conditions (see GNU "
                     "General Public License, Version 3 or later)")
        logging.info(Sync._DELIMITER)
        logging.debug("Started synchronization script")

        # Warn if user selected --delete option
        if self.args.delete:
            logging.warning("--delete option may lead to loss of data")
            time.sleep(2.0)

    def _setup_logger(self):
        """Setup the logging functionality."""
        if self.args.verbose:
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.INFO)

        # Add NullHandler (required when no output is desired)
        null_handler = logging.NullHandler()
        self.logger.addHandler(null_handler)

        # Real handlers
        if not self.args.no_logfile:
            file_log_handler = logging.FileHandler(self.args.logfile, mode='a')
            file_log_handler.setFormatter(Sync._LOG_FORMATTER)
            self.logger.addHandler(file_log_handler)
        if not self.args.quiet:
            console_log_handler = logging.StreamHandler()
            console_log_handler.setFormatter(Sync._LOG_FORMATTER)
            self.logger.addHandler(console_log_handler)

    def _read_config(self):
        """Get ConfigParser instance of the configuration file."""
        # Read configuration file
        if not os.path.isfile(self.args.configfile):
            logging.error("Configuraton file '%s' does not exist",
                          self.args.configfile)
            exit(1)
        self.config.read(self.args.configfile)
        logging.debug("Successfully read configuration file '%s'",
                      self.args.configfile)

        # Process possible aliases
        for (i, old_host) in enumerate(self.args.targets):
            if self.config.has_option(Sync._ALIASES, old_host):
                new_host = self.config.get(Sync._ALIASES, old_host)
                self.args.targets[i] = new_host
                logging.info("Aliased '%s' to '%s'", old_host, new_host)

    def _log_sync(self, out, src, dest):
        """Log the actual synchronization process."""
        info_list = []
        for info in out.split('\n')[1:]:
            # Arbitrary information
            if (any(['\r' in info,
                     info == '',
                     info == './',
                     info.startswith('sent '),
                     info.startswith('total size is')])):
                pass

            # New directory created
            elif 'created directory' in info:
                info = info.replace("created directory ",
                                    "Created directory '") + "'"
                info_list.append('    ' + info)

            # File deleted
            elif info.startswith('deleting'):
                info = info.replace('deleting ', '', 1)
                d_str = (dest+info if dest.endswith('/') else dest)
                if self.args.dry_run:
                    prefix = "    Would delete "
                else:
                    prefix = "    Deleted "
                info_list.append(prefix + "'{}'".format(d_str))

            # File moved
            else:
                s_str = (src+info if src.endswith('/') else src)
                d_str = (dest+info if dest.endswith('/') else dest)
                if self.args.dry_run:
                    prefix = "    Would move "
                else:
                    prefix = "    Successfully moved "
                info_list.append(prefix + "'{}' to '{}'".format(s_str, d_str))
        return list(set(info_list))

    def _sync_command(self, element, direction, target_host):
        """Perform synchronization command."""
        # Get _PATH options if available
        if self.config.has_option(target_host, Sync._PATH):
            target_host_path = os.path.expanduser(
                self.config.get(target_host, Sync._PATH))
        else:
            target_host_path = target_host + ':./'

        # Get elements
        this_element = os.path.expanduser(
            '~/' + self.config.get(self.this_host, element))
        target_element = target_host_path + \
            self.config.get(target_host, element)

        # Get synchronization command
        if direction == 'up':
            src = this_element
            dest = target_element
        elif direction == 'down':
            src = target_element
            dest = this_element
        command_str = self.sync_command + ' ' + src + ' ' + dest
        logging.debug("Performing command '%s'", command_str)

        # Actual synchronization
        comm = subprocess.Popen(command_str, shell=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        (out, err) = comm.communicate()
        out = out.decode('utf-8')
        err = err.decode('utf-8')
        return (src, dest, out, err)

    def _process_host(self, target_host, direction):
        """Perform synchronization to host."""
        # Get direction
        prefix = 'Upload' if direction == 'up' else 'Download'

        # Get elements
        elements_this = self.config.options(self.this_host)
        elements_target = self.config.options(target_host)

        # Check if the hosts share elements
        if set(elements_this).isdisjoint(elements_target):
            logging.info("    %s: the two hosts do not share common elements",
                         prefix)
            return True

        # Iterate through elements
        successful = True
        for element in elements_this:
            if element == Sync._PATH:
                continue
            if element in elements_target:
                (src, dest, out, err) = self._sync_command(element, direction,
                                                           target_host)
                info_list = self._log_sync(out, src, dest)

                # Log operations
                if out:
                    logging.debug(out)
                if info_list:
                    for info in info_list:
                        logging.info(info)
                if ('Connection refused' in err or
                        'Could not resolve hostname' in err):
                    logging.error("   %s: cannot connect to host '%s'", prefix,
                                  target_host)
                    return False
                if err:
                    logging.warning(" %s", err.strip('\n').replace('\n', '; '))
                    successful = False

        return successful

    def _perform_sync(self):
        """Perform synchronization process."""
        # Perform pre-command
        logging.debug("Performing pre-command: '%s'", Sync._PRE_COMMAND)
        pre_comm = subprocess.Popen(Sync._PRE_COMMAND, shell=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
        (out, err) = pre_comm.communicate()
        out = out.decode('utf-8')
        err = err.decode('utf-8')
        if out:
            logging.info(out.strip('\n'))
        if err:
            logging.error(err.strip('\n'))
        logging.info("")

        # Get synchronization direction
        if self.args.up == self.args.down:
            perform_up = True
            perform_down = True
        else:
            perform_up = self.args.up
            perform_down = self.args.down

        # Loop over all target hosts
        for target_host in self.target_hosts:
            logging.debug("Started synchronization between '%s' and '%s'",
                          self.this_host, target_host)

            # Sync
            success_up = True
            success_down = True
            if perform_up:
                logging.info("Started upload '%s' --> '%s'", self.this_host,
                             target_host)
                success_up = self._process_host(target_host, 'up')
            if perform_down:
                logging.info("Started download '%s' --> '%s'", target_host,
                             self.this_host)
                success_down = self._process_host(target_host, 'down')
            successful = (success_up and success_down)

            # Log
            prefix = "Simulated" if self.args.dry_run else "Completed"
            if successful:
                logging.info("%s synchronization between '%s' and '%s'",
                             prefix, self.this_host, target_host)
            else:
                logging.warning(
                    "%s synchronization between '%s' and '%s' with error(s)",
                    prefix, self.this_host, target_host)
            logging.info("")

        logging.debug("Finished synchronization script")
        logging.info("%s\n", Sync._DELIMITER)

    def print_help(self):
        """Print help messages."""
        self.parser.print_help()

    def run(self):
        """Start synchronization procedure."""
        self._parse_args()
        self._setup_logger()
        self._print_welcome()
        self._read_config()
        self._get_hosts()
        self._perform_sync()


# Call script directly
if __name__ == '__main__':
    SYNC_INSTANCE = Sync()
    SYNC_INSTANCE.run()

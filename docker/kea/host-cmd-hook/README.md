# Kea with Host Commands Hook

This directory contains files for building the Kea and a Python interpreter hook. This hook provides a thin wrapper around a subset of Kea classes, enabling the development of hooks in Python.

## Repositories

The original repository for this hook can be found at [davejohncole/kea_python](https://github.com/davejohncole/kea_python).

The `Dockerfile` uses a forked version compatible with Kea version 2.4 is located at [invite-networks/kea_python](https://github.com/invite-networks/kea_python).

## Integration with PowerDNS

The Python hook has been updated to work with PowerDNS for host reservations. The `keahook.py` file is sourced from the original repository and later modified.

## Premium Host Commands Hook

ISC sells a premium version of the Kea host commands hook.

## Limitation

One drawback of this integration is that Kea must be run in single-threaded mode.

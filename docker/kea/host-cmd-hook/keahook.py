from os import environ
import kea
import json
import requests

# This hook was improved by ChatGPT and later some errors were resolved.

powerdns_domain = environ["PDNS_DOMAIN"]
powerdns_api_key = environ["PDNS_API_KEY"]
powerdns_api_host = environ["PDNS_API_HOST"]
powerdns_api_port = environ["PDNS_API_PORT"]
powerdns_api_protocol = environ["PDNS_API_PROTOCOL"]
powerdns_api_url = f"{powerdns_api_protocol}://{powerdns_api_host}:{powerdns_api_port}/api/v1"

class UNSPECIFIED:
    pass


class CommandError(Exception):

    def __init__(self, reason):
        self.reason = reason


def get_arg(args, name, default=UNSPECIFIED, error_msg=None):
    if args is None or name not in args:
        if default is not UNSPECIFIED:
            return default
        if error_msg:
            raise CommandError(error_msg)
        raise CommandError("'%s' parameter not specified" % name)
    return args[name]


def get_string_arg(args, name, default=UNSPECIFIED, error_msg=None):
    value = get_arg(args, name, default, error_msg)
    if value != default and not isinstance(value, str):
        if error_msg:
            raise CommandError(error_msg)
        raise CommandError("'%s' is not a string" % name)
    return value


def get_int_arg(args, name, default=UNSPECIFIED, error_msg=None):
    value = get_arg(args, name, default)
    if value != default and not isinstance(value, int):
        if error_msg:
            raise CommandError(error_msg)
        raise CommandError("'%s' is not an integer" % name)
    return value


def get_map_arg(args, name, default=UNSPECIFIED, error_msg=None):
    value = get_arg(args, name, default)
    if value != default and not isinstance(value, dict):
        if error_msg:
            raise CommandError(error_msg)
        raise CommandError("'%s' is not a map" % name)
    return value


def wrap_handler(handle, get_response):
    try:
        cmd = handle.getArgument('command')
        args = cmd.get('arguments')
        if args is not None and not isinstance(args, dict):
            raise CommandError('parameters missing or is not a map')
        handle.setArgument('response', get_response(args))
    except CommandError as e:
        handle.setArgument('response', {'result': 1,
                                        'text': e.reason})
        return 1
    except Exception as e:
        kea.logger.exception('')
        handle.setArgument('response', {'result': 1,
                                        'text': str(e)})
        return 1
    return 0

# Add dns record to PowerDNS via its API
def add_dns_record(host_ip, hostname):
    headers = {'X-API-Key': powerdns_api_key}
    payload = {
        "rrsets": [
            {
                "name": f"{hostname}.",
                "type": "A",
                "ttl": 600,
                "changetype": "REPLACE",
                "records": [
                    {
                        "content": host_ip,
                        "disabled": False
                    }
                ]
            }
        ]
    }

    response = requests.patch(
        f"{powerdns_api_url}/servers/localhost/zones/{powerdns_domain}.",
        headers=headers,
        data=json.dumps(payload)
    )

    if response.status_code not in [200, 201, 204]:
        raise Exception(f"Failed to add DNS record: {response.content}")

# Delete dns record from PowerDNS via its API
def delete_dns_record(hostname):
    headers = { 'X-API-Key': powerdns_api_key }

    data = {
        "rrsets": [
            {
                "name": f"{hostname}.{powerdns_domain}.",
                "type": "A",
                "changetype": "DELETE"
            }
        ]
    }

    response = requests.patch(
        f"{powerdns_api_url}/servers/localhost/zones/{powerdns_domain}.",
        headers=headers,
        data=json.dumps(data)
    )

    if response.status_code not in [200, 204]:
        raise Exception(f"Failed to delete DNS record: {response.content}")


def core_add_reservation(subnet_id, resv, host_mgr):
    unsupported_arguments = [ 'identifier-type' ]
    # Remove any unsupported keys from the reservation dictionary
    for key in unsupported_arguments:
        resv.pop(key, None)
    host = kea.HostReservationParser4().parse(subnet_id, resv)
    host_mgr.add(host)
    ip_address = resv.get('ip-address')
    hostname = resv.get('hostname')
    fqdn = f"{hostname}.{powerdns_domain}"
    try:
        add_dns_record(ip_address, fqdn)
    except Exception as e:
        return {'result': 1, 'text': f"Failed to add DNS record: {e}"}
    return {'result': 0, 'text': f"Host reservation added: MAC={resv.get('hw-address')}, IP={ip_address}"}

def core_del_reservation(subnet_id, args, host_mgr):
    identifier_type = args.get('identifier-type')
    # Explicitly get 'identifier' or 'hw-address'
    identifier = args.get('identifier') or args.get('hw-address')
    if identifier is None:
        return {'result': 1, 'text': f"Identifier not provided for identifier type {identifier_type}"}

    was_deleted = host_mgr.del4(subnet_id, identifier_type, identifier)
    if was_deleted:
        hostname = args.get('hostname')
        try:
            delete_dns_record(hostname)
        except Exception as e:
            return {'result': 1, 'text': f"Failed to delete DNS record: {e}"}
        return {'result': 0, 'text': 'Host deleted.'}
    return {'result': 1, 'text': 'Host not deleted (not found).'}

# {"command": "reservation-add",
#  "arguments": {"reservation": {"subnet-id": 1,
#                                reservation attribs}}}
def reservation_add(handle):
    def get_response(args):
        resv = get_map_arg(args, 'reservation')
        subnet_id = get_int_arg(resv, 'subnet-id')
        del resv['subnet-id']
        host_mgr = kea.HostMgr.instance()
        return core_add_reservation(subnet_id, resv, host_mgr)
    return wrap_handler(handle, get_response)


# {"command": "reservation-get",
#  "arguments": {"subnet-id": 1,
#                "ip-address": "192.0.2.202"}}
# {"command": "reservation-get",
#  "arguments": {"subnet-id": 4,
#                "identifier-type": "hw-address",
#                "identifier": "01:02:03:04:05:06"}}
def reservation_get(handle):
    def get_response(args):
        host_mgr = kea.HostMgr.instance()
        subnet_id = get_int_arg(args, 'subnet-id')
        if 'ip-address' in args:
            ip_address = get_string_arg(args, 'ip-address')
            host = host_mgr.get(subnet_id, ip_address)
        else:
            identifier_type = get_string_arg(args, 'identifier-type')
            identifier = get_string_arg(args, 'identifier')
            host = host_mgr.get(subnet_id, identifier_type, identifier)
        if host is None:
            return {'result': 0, 'text': 'Host not found.'}
        else:
            return {'result': 0,
                    'text': 'Host found.',
                    'arguments': host.toElement()}

    return wrap_handler(handle, get_response)


# {"command": "reservation-get-all",
#  "arguments": {"subnet-id": 1}}
def reservation_get_all(handle):
    def get_response(args):
        subnet_id = get_int_arg(args, 'subnet-id')
        hosts = kea.HostMgr.instance().getAll4(subnet_id)
        return {'result': 0,
                'text': '%s IPv4 host(s) found.' % len(hosts),
                'arguments': {'hosts': [h.toElement() for h in hosts]}}

    return wrap_handler(handle, get_response)


# {"command": "reservation-get-page",
#  "arguments": {"subnet-id": 1,
#                "limit": 10}}
# { "command": "reservation-get-page",
#   "arguments": {"subnet-id": 1,
#                 "source-index": 1,
#                 "from": 1234567,
#                 "limit": 10}}
def reservation_get_page(handle):
    def get_response(args):
        host_mgr = kea.HostMgr.instance()
        subnet_id = get_int_arg(args, 'subnet-id')
        source_index = get_int_arg(args, 'source-index', 0)
        lower_host_id = get_int_arg(args, 'from', 0)
        page_size = get_int_arg(args, 'limit')
        hosts, source_index = host_mgr.getPage4(subnet_id, source_index, lower_host_id, page_size)
        if hosts:
            return {'result': 0,
                    'text': '%s IPv4 host(s) found.' % len(hosts),
                    'arguments': {'count': len(hosts),
                                  'hosts': [h.toElement() for h in hosts],
                                  'next': {'from': hosts[-1].getHostId(),
                                           'source-index': source_index}}}
        return {'result': 3,
                'text': '0 IPv4 host(s) found.',
                'arguments': {'count': 0,
                              'hosts': []}}

    return wrap_handler(handle, get_response)


# {"command": "reservation-del",
#  "arguments": {"subnet-id": 1,
#                "ip-address": "192.0.2.202"}}
# {"command": "reservation-del",
#  "arguments": {"subnet-id": 4,
#                "identifier-type": "hw-address",
#                "identifier": "01:02:03:04:05:06"}}
def reservation_del(handle):
    def get_response(args):
        subnet_id = get_int_arg(args, 'subnet-id')
        host_mgr = kea.HostMgr.instance()
        return core_del_reservation(subnet_id, args, host_mgr)
    return wrap_handler(handle, get_response)

def reservation_update(handle):
    def get_response(args):
        resv = get_map_arg(args, 'reservation')
        subnet_id = get_int_arg(resv, 'subnet-id')
        del resv['subnet-id']
        host_mgr = kea.HostMgr.instance()

        hw_address = resv.get('hw-address')
        if hw_address is None:
            return {'result': 1, 'text': 'hw-address not provided'}

        # Ensure that hw_address is a string
        if not isinstance(hw_address, str):
            return {'result': 1, 'text': 'hw-address must be a string'}

        # First delete the old reservation
        del_response = core_del_reservation(subnet_id, {'identifier-type': 'hw-address', 'identifier': hw_address}, host_mgr)
        if del_response['result'] != 0:
            return del_response

        # Then add the new reservation
        return core_add_reservation(subnet_id, resv, host_mgr)

    return wrap_handler(handle, get_response)


def load(handle):
    handle.registerCommandCallout('reservation-add', reservation_add)
    handle.registerCommandCallout('reservation-get', reservation_get)
    handle.registerCommandCallout('reservation-get-all', reservation_get_all)
    handle.registerCommandCallout('reservation-get-page', reservation_get_page)
    handle.registerCommandCallout('reservation-del', reservation_del)
    handle.registerCommandCallout('reservation_update', reservation_update)
    return 0

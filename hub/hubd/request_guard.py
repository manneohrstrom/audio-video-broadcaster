"""Which HTTP requests hubd answers: its own pages and local tools, not other websites.

The hub has no login, so without this any site open in a browser on the studio network
could press STOP or delete a take. Two checks:

  Host    must be a local name or an IP address. A site that points its own name at the
          hub's address (DNS rebinding) would otherwise count as the hub's own page.
  Origin  browsers add it to every POST and PUT. It must be the hub's own address.
          Tools (curl, hub-ingest, the deploy's guard) send none.
"""
import ipaddress
import urllib.parse

LOCAL_SUFFIXES = (".local", ".lan", ".home.arpa")      # names public DNS can't hand out


def is_local_host(host):
    """True for a Host header naming an IP address, a single label or a local-only name."""
    try:
        name = (urllib.parse.urlsplit("//" + host).hostname or "").rstrip(".")
    except ValueError:
        return False
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return "." not in name or name.endswith(LOCAL_SUFFIXES)


def refusal(headers):
    """Why this request is refused, or None to answer it."""
    host = headers.get("Host")
    if host is not None and not is_local_host(host):
        return "open the hub by its .local name or its IP address"
    origin = headers.get("Origin")
    if origin is None:
        return None
    if host and origin.lower() in ("http://" + host.lower(), "https://" + host.lower()):
        return None
    return "requests from other websites are refused"

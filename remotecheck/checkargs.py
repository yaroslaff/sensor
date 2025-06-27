checkargs = {
    'httpstatus': {
        "required":['url', 'status'],
        "optional": ['options'],
    },

    'sslcert': {
        "required":['host'],
        "optional": ['port', 'days'],
    },
    
    'sha1static': {
        "required":['url'],
        "optional": ['hash', 'options'],
    },

    'sha1dynamic': {
        "required":['url'],
        "optional": ['hash', 'options'],
    },

    "ping": {
        "required":['host'],
        "optional": [],
        "description": "Ping <host>"
    },

    "tcpport": {
        "required":['host', 'port'],
        "optional": ['substr']
    },

    "httpgrep": {
        "required":['url'],
        "optional": ['musthave', 'mustnothave', 'options']
    },

    "whois": {
        "required":['domain'],
        "optional": ['days']
    },

    "dns": {
        "required":['host', 'type', 'value'],
        "optional": ['options']
    },

    "dnsbl": {
        "required": ["host"],
        "optional": ["skip", "extra"]
    }
}
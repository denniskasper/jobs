"""The bookmark button: a `javascript:` address which, kept in the bookmarks bar and pressed on
another site's page, opens the page that creates an Application with that page's title, address
and selection. Nothing about that page reaches the server but what the browser sends."""

import json

# The longest address the button opens. The server refuses a request whose line and headers pass
# 16 KiB together (h11's limit); half of it leaves the headers their room.
ADDRESS_LIMIT = 8000

# The selection is cut, a whole character at a time, to the most that keeps the address within
# the limit, and `cut` then tells the form so. Written without line breaks: a browser drops
# them from an address, and the statements are kept apart by semicolons alone.
_SCRIPT = (
    "(function(){"
    "var target=%(target)s,limit=%(limit)d;"
    "var chars=Array.from(String(getSelection()));"
    "function address(n){"
    "return target+'?title='+encodeURIComponent(document.title)"
    "+'&link='+encodeURIComponent(location.href)"
    "+'&text='+encodeURIComponent(chars.slice(0,n).join(''))"
    "+(n<chars.length?'&cut=1':'');}"
    "var n=chars.length;"
    "if(address(n).length>limit){"
    "var low=0,high=n-1;"
    "while(low<high){var middle=Math.ceil((low+high)/2);"
    "if(address(middle).length<=limit)low=middle;else high=middle-1;}"
    "n=low;}"
    "location.href=address(n);"
    "})()"
)


def button(target: str) -> str:
    """The button's address, opening `target`: the page that creates an Application, at the
    address the request for the page carrying the button reached it at."""
    script = _SCRIPT % {"target": json.dumps(target), "limit": ADDRESS_LIMIT}
    # The browser percent-decodes a `javascript:` address before it runs it.
    return "javascript:" + script.replace("%", "%25")

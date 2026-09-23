"""Small real PNG and a standard-library parser for standalone HTML assertions."""
import base64
from html.parser import HTMLParser


PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH1sAAAAASUVORK5CYII=')


class DashboardHTML(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.text = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)

    @property
    def images(self):
        return [attributes['src'] for tag, attributes in self.elements if tag == 'img']

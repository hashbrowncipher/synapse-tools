import SimpleHTTPServer
import SocketServer
import logging

PORT = 1999

class BlockingGetHandler(SimpleHTTPServer.SimpleHTTPRequestHandler):

    def do_GET(self):
        logging.warning(self.headers)
        if 'x-mode' not in self.headers.keys():
            self.send_response(500)
        else:
            SimpleHTTPServer.SimpleHTTPRequestHandler.do_GET(self)


class GetHandler(SimpleHTTPServer.SimpleHTTPRequestHandler):

    def do_GET(self):
        logging.warning(self.headers)
        SimpleHTTPServer.SimpleHTTPRequestHandler.do_GET(self)

httpd = SocketServer.TCPServer(("0.0.0.0", PORT), BlockingGetHandler)

httpd.serve_forever()

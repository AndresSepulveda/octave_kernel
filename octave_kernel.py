from IPython.kernel.zmq.kernelbase import Kernel
from oct2py import octave, Oct2PyError

import os
import signal
from subprocess import check_output
import re

__version__ = '0.1'

version_pat = re.compile(r'version (\d+(\.\d+)+)')


class OctaveKernel(Kernel):
    implementation = 'octave_kernel'
    implementation_version = __version__
    language = 'octave'

    @property
    def language_version(self):
        m = version_pat.search(self.banner)
        return m.group(1)

    _banner = None

    @property
    def banner(self):
        if self._banner is None:
            self._banner = check_output(['octave',
                                         '--version']).decode('utf-8')
        return self._banner

    def __init__(self, **kwargs):
        Kernel.__init__(self, **kwargs)
        # Signal handlers are inherited by forked processes,
        # and we can't easily reset it from the subprocess.
        # Since kernelapp ignores SIGINT except in message handlers,
        # we need to temporarily reset the SIGINT handler here
        # so that octave and its children are interruptible.
        sig = signal.signal(signal.SIGINT, signal.SIG_DFL)
        try:
            self.octavewrapper = octave
        finally:
            signal.signal(signal.SIGINT, sig)

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=False):
        code = code.strip()
        abort_msg = {'status': 'abort', 
                     'execution_count': self.execution_count}
        if not code or code == 'keyboard' or code.startswith('keyboard('):
            return {'status': 'ok', 'execution_count': self.execution_count,
                    'payload': [], 'user_expressions': {}}
        elif (code == 'exit' or code.startswith('exit(')
                or code == 'quit' or code.startswith('quit(')):
            # TODO: exit gracefully here
            self.do_shutdown(False)
            return abort_msg
        elif code == 'restart':
            self.octavewrapper.restart()
            return abort_msg
        elif code.endswith('?'):
            code = self._get_help(code)
            if not code:
                return abort_msg
        interrupted = False
        try:
            output = self.octavewrapper._eval([code])
        except KeyboardInterrupt:
            self.octavewrapper._session.proc.send_signal(signal.SIGINT)
            interrupted = True
            output = 'Octave Session Interrupted'
        except Oct2PyError as e:
            return self._handle_error(str(e))
        else:
            if output is None:
                output = ''
            elif output == 'Octave Session Interrupted':
                interrupted = True

        if not silent:
            stream_content = {'name': 'stdout', 'data': output}
            self.send_response(self.iopub_socket, 'stream', stream_content)

        if interrupted:
            return abort_msg

        return {'status': 'ok', 'execution_count': self.execution_count,
                'payload': [], 'user_expressions': {}}

    def do_complete(self, code, cursor_pos):
        code = code[:cursor_pos]
        default = {'matches': [], 'cursor_start': 0,
                   'cursor_end': cursor_pos, 'metadata': dict(),
                   'status': 'ok'}
        if code[-1] == ' ':
            return default
        tokens = code.replace(';', ' ').split()
        if not tokens:
            return default
        token = tokens[-1]
        if os.sep in token:
            dname = os.path.dirname(token)
            rest = os.path.basename(token)
            if os.path.exists(dname):
                files = os.listdir(dname)
                matches = [f for f in files if f.startswith(rest)]
                start = cursor_pos - len(rest)
            else:
                return default
        else:
            start = cursor_pos - len(token)
            cmd = 'completion_matches("%s")' % token
            output = self.octavewrapper._eval([cmd])
            matches = output.split()
            for item in dir(self.octavewrapper):
                if item.startswith(token) and not item in matches:
                    matches.append(item)
        return {'matches': matches, 'cursor_start': start,
                'cursor_end': cursor_pos, 'metadata': dict(),
                'status': 'ok'}

    def do_shutdown(self, restart):
        if restart:
            self.octavewrapper.restart()
        else:
            self.octavewrapper.close()
        return Kernel.do_shutdown(self, restart)

    def _get_help(self, code):
        if code[:-1] in dir(self.octavewrapper):
            output = getattr(self.octavewrapper, code[:-1]).__doc__
            stream_content = {'name': 'stdout', 'data': output}
            self.send_response(self.iopub_socket, 'stream', stream_content)
            code = None
        elif code.endswith('??') and code[:-2] in dir(self.octavewrapper):
            output = getattr(self.octavewrapper, code[:-2]).__doc__
            stream_content = {'name': 'stdout', 'data': output}
            self.send_response(self.iopub_socket, 'stream', stream_content)
            code = None
        else:
            if code.endswith('??'):
                code = 'help("' + code[:-2] + '")\n\ntype ' + code[:-2]
            else:
                code = 'help("' + code[:-1] + '")'
        return code

    def _handle_error(self, err):
        if 'parse error:' in err:
            err = 'Parse Error'
        elif 'Octave returned:' in err:
            err = err[err.index('Octave returned:'):]
            err = err[len('Octave returned:'):].lstrip()
        elif 'Syntax Error' in err:
            err = 'Syntax Error'
        stream_content = {'name': 'stdout', 'data': err.strip()}
        self.send_response(self.iopub_socket, 'stream', stream_content)
        return {'status': 'error', 'execution_count': self.execution_count,
                'ename': '', 'evalue': err, 'traceback': []}

if __name__ == '__main__':
    from IPython.kernel.zmq.kernelapp import IPKernelApp
    IPKernelApp.launch_instance(kernel_class=OctaveKernel)

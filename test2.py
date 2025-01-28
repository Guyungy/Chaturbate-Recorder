import time
import datetime
import os
import threading
import sys
import configparser
import streamlink
import subprocess
import queue
import requests
import signal
import shutil
from pathlib import Path

# Windows控制台颜色支持
if os.name == 'nt':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)

# 全局配置
CONFIG_FILE = 'config.conf'
DEFAULT_CONFIG = {
    'paths': {
        'wishlist': './wanted.txt',
        'save_directory': './captures',
        'completed_directory': '',
        'directory_structure': '{path}/{model}/{year}.{month}.{day}_{hour}.{minute}.{second}_{model}.mp4'
    },
    'settings': {
        'checkInterval': '20',
        'genders': 'female, couple',
        'postProcessingCommand': '',
        'postProcessingThreads': '1',
        'ffmpeg_path': ''
    },
    'login': {
        'username': '',
        'password': ''
    }
}

class ConfigManager:
    def __init__(self):
        self.config = configparser.ConfigParser()
        self.settings = {}
        self.load_config()

    def create_default_config(self):
        self.config.read_dict(DEFAULT_CONFIG)
        with open(CONFIG_FILE, 'w') as f:
            self.config.write(f)

    def load_config(self):
        # 创建默认配置
        if not os.path.exists(CONFIG_FILE):
            self.create_default_config()
        
        self.config.read(CONFIG_FILE)

        # 确保所有section和option存在
        for section in DEFAULT_CONFIG.keys():
            if not self.config.has_section(section):
                self.config.add_section(section)
            for opt, val in DEFAULT_CONFIG[section].items():
                if not self.config.has_option(section, opt):
                    self.config.set(section, opt, val)
        
        # 保存修正后的配置
        with open(CONFIG_FILE, 'w') as f:
            self.config.write(f)

        # 读取配置
        self.settings = {
            'wishlist': self.config.get('paths', 'wishlist'),
            'save_dir': self.config.get('paths', 'save_directory'),
            'completed_dir': self.config.get('paths', 'completed_directory'),
            'directory_template': self.config.get('paths', 'directory_structure'),
            'check_interval': self.config.getint('settings', 'checkInterval'),
            'genders': [g.strip().lower() for g in self.config.get('settings', 'genders').split(',')],
            'post_cmd': self.config.get('settings', 'postProcessingCommand'),
            'post_threads': self.config.getint('settings', 'postProcessingThreads'),
            'ffmpeg_path': self.config.get('settings', 'ffmpeg_path'),
            'username': self.config.get('login', 'username'),
            'password': self.config.get('login', 'password')
        }

        # 自动检测FFmpeg路径
        if not self.settings['ffmpeg_path']:
            self.settings['ffmpeg_path'] = shutil.which('ffmpeg') or ''

        # 创建保存目录
        Path(self.settings['save_dir']).mkdir(parents=True, exist_ok=True)

class Recorder(threading.Thread):
    def __init__(self, model, config):
        super().__init__(name=f"Recorder-{model}")
        self.model = model
        self.config = config
        self.stop_event = threading.Event()
        self.process = None
        self.file_path = None
        self.gender = None
        self.stream_url = None

    def generate_filepath(self):
        now = datetime.datetime.now()
        params = {
            'path': self.config.settings['save_dir'],
            'model': self.model,
            'year': now.strftime("%Y"),
            'month': now.strftime("%m"),
            'day': now.strftime("%d"),
            'hour': now.strftime("%H"),
            'minute': now.strftime("%M"),
            'second': now.strftime("%S"),
            'gender': self.gender.capitalize() if self.gender else 'Unknown'
        }
        return Path(self.config.settings['directory_template'].format(**params))

    def run(self):
        try:
            stream_info = self.get_stream_info()
            if not stream_info or not self.validate_gender(stream_info.get('gender', '')):
                return

            self.gender = stream_info['gender']
            file_path = self.generate_filepath()
            file_path.parent.mkdir(parents=True, exist_ok=True)

            ffmpeg_cmd = [
                self.config.settings['ffmpeg_path'],
                '-y', '-loglevel', 'warning',
                '-i', '-', '-c', 'copy',
                '-f', 'mp4', str(file_path)
            ]

            self.process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            session = streamlink.Streamlink()
            if self.config.settings['username'] and self.config.settings['password']:
                session.set_plugin_option('chaturbate', 'username', self.config.settings['username'])
                session.set_plugin_option('chaturbate', 'password', self.config.settings['password'])

            stream = session.streams(f'hlsvariant://{self.stream_url}')['best']
            fd = stream.open()

            while not self.stop_event.is_set():
                try:
                    data = fd.read(1024 * 8)
                    if not data:
                        break
                    self.process.stdin.write(data)
                except (IOError, OSError):
                    break

            self.process.stdin.close()
            if self.process.wait(10) == 0:
                self.post_process(file_path)
            else:
                self.handle_error(file_path)

        except Exception as e:
            self.log_error(f"Recording error: {str(e)}")
        finally:
            self.cleanup()

    def get_stream_info(self):
        try:
            resp = requests.get(
                f'https://chaturbate.com/api/chatvideocontext/{self.model}/',
                timeout=10
            )
            data = resp.json()
            self.stream_url = data.get('hls_source', '')
            return {
                'gender': data.get('broadcaster', {}).get('gender', '').lower(),
                'status': data.get('status', 'offline')
            }
        except Exception as e:
            self.log_error(f"Stream check failed: {str(e)}")
            return None

    def validate_gender(self, stream_gender):
        return stream_gender in self.config.settings['genders']

    def post_process(self, file_path):
        if self.config.settings['post_cmd']:
            processing_queue.put({
                'path': str(file_path),
                'model': self.model,
                'gender': self.gender,
                'completed_dir': self.config.settings['completed_dir']
            })

    def handle_error(self, file_path):
        error_log = self.process.stderr.read().decode('utf-8', 'ignore')
        self.log_error(f"FFmpeg failed:\n{error_log}")
        try:
            if file_path.exists():
                file_path.unlink()
        except Exception as e:
            self.log_error(f"Cleanup failed: {str(e)}")

    def cleanup(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def log_error(self, message):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open('errors.log', 'a') as f:
            f.write(f"[{timestamp}] {self.model}: {message}\n")

    def stop(self):
        self.stop_event.set()
        if self.is_alive():
            self.join(timeout=10)

class Processor(threading.Thread):
    def __init__(self, task_queue, config):
        super().__init__(daemon=True)
        self.task_queue = task_queue
        self.config = config

    def run(self):
        while True:
            try:
                task = self.task_queue.get(timeout=5)
                self.process_file(task)
            except queue.Empty:
                if threading.main_thread().is_alive():
                    continue
                break

    def process_file(self, task):
        try:
            input_path = Path(task['path'])
            cmd = self.build_command(input_path, task)

            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=3600
            )

            if result.returncode == 0:
                self.move_file(input_path, task['completed_dir'])

        except subprocess.CalledProcessError as e:
            self.log_error(f"Processing failed: {e.stdout.decode()}")
        except Exception as e:
            self.log_error(f"Unexpected error: {str(e)}")

    def build_command(self, input_path, task):
        cmd_template = self.config.settings['post_cmd'].split()
        params = {
            'input': str(input_path),
            'output': str(input_path),
            'filename': input_path.name,
            'directory': str(input_path.parent),
            'model': task['model'],
            'gender': task['gender']
        }
        return [part.format(**params) for part in cmd_template]

    def move_file(self, file_path, completed_dir):
        if completed_dir:
            dest_dir = Path(completed_dir) / file_path.parent.relative_to(self.config.settings['save_dir'])
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(file_path), str(dest_dir / file_path.name))

    def log_error(self, message):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open('processing_errors.log', 'a') as f:
            f.write(f"[{timestamp}] {message}\n")

class Controller:
    def __init__(self):
        self.config = ConfigManager()
        self.active_recorders = {}
        self.processing_queue = queue.Queue()
        self.workers = []
        self.stop_event = threading.Event()
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def start(self):
        # 启动处理线程
        for _ in range(self.config.settings['post_threads']):
            worker = Processor(self.processing_queue, self.config)
            worker.start()
            self.workers.append(worker)

        # 主循环
        while not self.stop_event.is_set():
            try:
                self.update_wishlist()
                self.cleanup_recorders()
                self.print_status()
                time.sleep(1)
            except KeyboardInterrupt:
                self.shutdown()

    def update_wishlist(self):
        try:
            with open(self.config.settings['wishlist'], 'r') as f:
                models = {line.strip().lower() for line in f if line.strip()}

            # 启动新录制
            for model in models - set(self.active_recorders.keys()):
                recorder = Recorder(model, self.config)
                recorder.start()
                self.active_recorders[model] = recorder

            # 停止失效录制
            for model in set(self.active_recorders.keys()) - models:
                self.active_recorders[model].stop()
                del self.active_recorders[model]

        except Exception as e:
            print(f"Error updating wishlist: {str(e)}")

    def cleanup_recorders(self):
        dead = [model for model, r in self.active_recorders.items() if not r.is_alive()]
        for model in dead:
            del self.active_recorders[model]

    def print_status(self):
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"Active Recorders: {len(self.active_recorders)}")
        print(f"Queued Tasks: {self.processing_queue.qsize()}")
        print("Currently Recording:")
        for model, recorder in self.active_recorders.items():
            status = "●" if recorder.is_alive() else "○"
            print(f"  {status} {model} ({recorder.gender or 'unknown'})")

    def shutdown(self, signum=None, frame=None):
        print("\nShutting down gracefully...")
        self.stop_event.set()

        for recorder in self.active_recorders.values():
            recorder.stop()

        while not self.processing_queue.empty():
            time.sleep(1)

        for worker in self.workers:
            if worker.is_alive():
                worker.join(timeout=5)

        sys.exit(0)

if __name__ == '__main__':
    controller = Controller()
    controller.start()
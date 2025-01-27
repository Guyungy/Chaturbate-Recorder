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

if os.name == 'nt':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)

mainDir = os.path.dirname(os.path.abspath(__file__))
Config = configparser.ConfigParser()
setting = {}

# 使用线程安全的队列和锁
recording = []
hilos = []
stop_event = threading.Event()

def cls():
    os.system('cls' if os.name == 'nt' else 'clear')

def readConfig():
    global setting
    Config.read(mainDir + '/config.conf')

    setting = {
        'save_directory': Config.get('paths', 'save_directory'),
        'wishlist': Config.get('paths', 'wishlist'),
        'interval': int(Config.get('settings', 'checkInterval')),
        'postProcessingCommand': Config.get('settings', 'postProcessingCommand')
    }

    try:
        setting['postProcessingThreads'] = int(Config.get('settings', 'postProcessingThreads').strip() or 1)
    except (configparser.NoOptionError, ValueError):
        setting['postProcessingThreads'] = 1

    os.makedirs(setting["save_directory"], exist_ok=True)

def postProcess(processingQueue):
    while not stop_event.is_set():
        try:
            parameters = processingQueue.get(timeout=1)
            model = parameters['model']
            path = parameters['path']
            filename = os.path.split(path)[-1]
            directory = os.path.dirname(path)
            file = os.path.splitext(filename)[0]
            subprocess.call(setting['postProcessingCommand'].split() + [path, filename, directory, model, file, 'cam4'])
        except queue.Empty:
            continue

class Modelo(threading.Thread):
    def __init__(self, modelo):
        super().__init__()
        self.modelo = modelo
        self._stop_event = threading.Event()
        self.file = None
        self.online = False
        self.lock = threading.Lock()
        self.fd = None  # 显式管理文件描述符

    def run(self):
        try:
            isOnline = self.isOnline()
            if not isOnline:
                self.online = False
                return

            self.online = True
            self.file = os.path.join(
                setting['save_directory'],
                self.modelo,
                f'{datetime.datetime.fromtimestamp(time.time()).strftime("%Y.%m.%d_%H.%M.%S")}_{self.modelo}.mp4'
            )

            session = streamlink.Streamlink()
            streams = session.streams(f'hlsvariant://{isOnline}')
            if 'best' not in streams:
                return

            stream = streams['best']
            self.fd = stream.open()

            with self.lock:
                if not isModelInListofObjects(self.modelo, recording):
                    os.makedirs(os.path.join(setting['save_directory'], self.modelo), exist_ok=True)
                    recording.append(self)

            with open(self.file, 'wb') as f:
                while not self._stop_event.is_set():
                    try:
                        data = self.fd.read(1024)
                        if not data:
                            break
                        f.write(data)
                    except (OSError, IOError):
                        break

        except Exception as e:
            self.log_exception(e)
        finally:
            self.cleanup()

    def cleanup(self):
        if self.fd:
            try:
                self.fd.close()
            except:
                pass
        with self.lock:
            try:
                recording.remove(self)
            except ValueError:
                pass
        self.check_invalid_file()

    def check_invalid_file(self):
        try:
            if self.file and os.path.isfile(self.file) and os.path.getsize(self.file) <= 1024:
                os.remove(self.file)
        except Exception as e:
            self.log_exception(e)

    def log_exception(self, e):
        with open('log.log', 'a+') as f:
            f.write(f'\n{datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")} EXCEPTION: {e}\n')

    def isOnline(self):
        try:
            resp = requests.get(f'https://chaturbate.com/api/chatvideocontext/{self.modelo}/', timeout=10)
            return resp.json().get('hls_source', '')
        except Exception as e:
            return ''

    def stop(self):
        self._stop_event.set()
        if self.fd:
            try:
                self.fd.close()
            except:
                pass
        self.join(timeout=5)

class CleaningThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.is_set():
            with threading.Lock():
                global hilos
                hilos = [hilo for hilo in hilos if hilo.is_alive()]
            time.sleep(10)

    def stop(self):
        self._stop_event.set()
        self.join()

class AddModelsThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.repeatedModels = []
        self.counterModel = 0

    def run(self):
        try:
            with open(setting['wishlist'], 'r') as f:
                wanted = [line.strip().lower() for line in f if line.strip()]
            
            seen = set()
            duplicates = set()
            unique_models = []
            for model in wanted:
                if model in seen:
                    duplicates.add(model)
                else:
                    seen.add(model)
                    unique_models.append(model)
            self.repeatedModels = list(duplicates)
            self.counterModel = len(unique_models)

            with threading.Lock():
                global hilos, recording
                existing_models = {h.modelo for h in hilos + recording}
                for model in unique_models:
                    if model not in existing_models:
                        thread = Modelo(model)
                        thread.start()
                        hilos.append(thread)

                for hilo in recording[:]:
                    if hilo.modelo not in seen:
                        hilo.stop()
        except Exception as e:
            print(f"Error updating models: {e}")

def isModelInListofObjects(obj, lista):
    return any(item.modelo == obj for item in lista)

def signal_handler(sig, frame):
    print("\nShutting down gracefully...")
    stop_event.set()
    
    # 停止所有线程
    for thread in hilos[:] + recording[:]:
        thread.stop()
    
    if 'cleaningThread' in globals():
        cleaningThread.stop()
    
    if 'postprocessingWorkers' in globals():
        for worker in postprocessingWorkers:
            worker.join(timeout=1)
    
    sys.exit(0)

if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    readConfig()

    processingQueue = queue.Queue()
    postprocessingWorkers = []
    if setting['postProcessingCommand']:
        for _ in range(setting['postProcessingThreads']):
            t = threading.Thread(target=postProcess, args=(processingQueue,))
            t.daemon = True
            t.start()
            postprocessingWorkers.append(t)

    cleaningThread = CleaningThread()
    cleaningThread.daemon = True
    cleaningThread.start()

    try:
        while not stop_event.is_set():
            addModelsThread = AddModelsThread()
            addModelsThread.start()
            addModelsThread.join()

            for remaining in range(setting['interval'], 0, -1):
                if stop_event.is_set():
                    break
                cls()
                if addModelsThread.repeatedModels:
                    print(f"Repeated models: {', '.join(addModelsThread.repeatedModels)}")
                print(f"{len(hilos):02d} alive Threads | Cleaning in {cleaningThread._stop_event.wait(timeout=1) or 10:02d}s | {addModelsThread.counterModel:02d} models")
                print(f"Online Threads: {len(recording):02d}")
                for hilo in recording:
                    print(f"  Recording: {hilo.modelo} -> {os.path.basename(hilo.file)}")
                print(f"Next check in {remaining:02d}s", end='\r')
                time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)
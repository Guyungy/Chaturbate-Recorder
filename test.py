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
import logging
import asyncio
import aiofiles

if os.name == 'nt':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)

logging.basicConfig(filename='recording.log', level=logging.INFO)

mainDir = sys.path[0]
Config = configparser.ConfigParser()
setting = {}

recording = []
hilos = []
processingQueue = queue.Queue()
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
        'postProcessingCommand': Config.get('settings', 'postProcessingCommand'),
    }
    try:
        setting['postProcessingThreads'] = int(Config.get('settings', 'postProcessingThreads'))
    except ValueError:
        if setting['postProcessingCommand'] and not setting.get('postProcessingThreads'):
            setting['postProcessingThreads'] = 1

    if not os.path.exists(setting['save_directory']):
        os.makedirs(setting['save_directory'])

def log_exception(e):
    logging.error(f'Exception occurred: {e}')

async def write_file(fd, file_path):
    async with aiofiles.open(file_path, 'wb') as f:
        while True:
            data = await fd.read(8192)
            if not data:
                break
            await f.write(data)

def reencode_video(input_file, output_file):
    command = [
        'ffmpeg',
        '-i', input_file,
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '23',
        '-c:a', 'aac',
        '-b:a', '192k',
        output_file
    ]
    subprocess.run(command)

def postProcess():
    while not stop_event.is_set():
        while processingQueue.empty():
            time.sleep(1)
        if stop_event.is_set():
            break
        parameters = processingQueue.get()
        model = parameters['model']
        path = parameters['path']
        filename = os.path.split(path)[-1]
        directory = os.path.dirname(path)
        file = os.path.splitext(filename)[0]
        command = setting['postProcessingCommand'].split() + [path, filename, directory, model, file, 'cam4']
        subprocess.call(command)

class Modelo(threading.Thread):
    def __init__(self, modelo):
        super().__init__()
        self.modelo = modelo
        self._stopevent = threading.Event()
        self.file = None
        self.online = None
        self.lock = threading.Lock()

    def run(self):
        global recording, hilos
        isOnline = self.isOnline()
        if not isOnline:
            self.online = False
        else:
            self.online = True
            self.file = os.path.join(setting['save_directory'], self.modelo, f'{datetime.datetime.now().strftime("%Y.%m.%d_%H.%M.%S")}_{self.modelo}.mp4')
            try:
                session = streamlink.Streamlink()
                streams = session.streams(f'hlsvariant://{isOnline}')
                stream = streams['best']
                fd = stream.open()
                fd.set_stream_buffer_size(1024 * 1024 * 50)  # 设置缓冲区大小为50MB
                if not isModelInListofObjects(self.modelo, recording):
                    os.makedirs(os.path.join(setting['save_directory'], self.modelo), exist_ok=True)
                    self.lock.acquire()
                    recording.append(self)
                    self.lock.release()
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(write_file(fd, self.file))
                    if setting['postProcessingCommand']:
                        processingQueue.put({'model': self.modelo, 'path': self.file})
            except Exception as e:
                log_exception(e)
                self.stop()
            finally:
                self.exceptionHandler()

    def exceptionHandler(self):
        self.stop()
        self.online = False
        self.lock.acquire()
        for index, hilo in enumerate(recording):
            if hilo.modelo == self.modelo:
                del recording[index]
                break
        self.lock.release()
        try:
            file = os.path.join(os.getcwd(), self.file)
            if os.path.isfile(file) and os.path.getsize(file) <= 1024:
                os.remove(file)
        except Exception as e:
            log_exception(e)

    def isOnline(self):
        try:
            resp = requests.get(f'https://chaturbate.com/api/chatvideocontext/{self.modelo}/')
            resp.raise_for_status()
            data = resp.json()
            hls_url = data.get('hls_source', '')
            if hls_url:
                return hls_url
            else:
                return False
        except requests.RequestException as e:
            log_exception(e)
            return False

    def stop(self):
        self._stopevent.set()

class CleaningThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.interval = 0
        self.lock = threading.Lock()

    def run(self):
        global hilos, recording
        while not stop_event.is_set():
            self.lock.acquire()
            new_hilos = []
            for hilo in hilos:
                if hilo.is_alive() or hilo.online:
                    new_hilos.append(hilo)
            hilos = new_hilos
            self.lock.release()
            for i in range(10, 0, -1):
                self.interval = i
                time.sleep(1)
                if stop_event.is_set():
                    break

class AddModelsThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.wanted = []
        self.lock = threading.Lock()
        self.repeatedModels = []
        self.counterModel = 0

    def run(self):
        global hilos, recording
        lines = open(setting['wishlist'], 'r').read().splitlines()
        self.wanted = (x for x in lines if x)
        self.lock.acquire()
        aux = []
        for model in self.wanted:
            model = model.lower()
            if model in aux:
                self.repeatedModels.append(model)
            else:
                aux.append(model)
                self.counterModel += 1
                if not isModelInListofObjects(model, hilos) and not isModelInListofObjects(model, recording):
                    thread = Modelo(model)
                    thread.start()
                    hilos.append(thread)
        for hilo in recording:
            if hilo.modelo not in aux:
                hilo.stop()
        self.lock.release()

def isModelInListofObjects(obj, lista):
    return any(i.modelo == obj for i in lista)

if __name__ == '__main__':
    readConfig()
    postprocessingWorkers = []
    if setting['postProcessingCommand']:
        for _ in range(setting['postProcessingThreads']):
            t = threading.Thread(target=postProcess)
            postprocessingWorkers.append(t)
            t.start()

    cleaningThread = CleaningThread()
    cleaningThread.start()

    try:
        while True:
            readConfig()
            addModelsThread = AddModelsThread()
            addModelsThread.start()
            addModelsThread.join()
            cls()
            i = setting['interval']
            while i > 0:
                cls()
                if addModelsThread.repeatedModels:
                    print(f'以下模型在愿望列表中重复出现：[{", ".join(modelo for modelo in addModelsThread.repeatedModels)}]')
                print(f'当前活动线程数（每个非录制模型一个线程）：{len(hilos):02d}，清理无效/不在线线程中，剩余时间：{cleaningThread.interval:02d}秒')
                print(f'在线线程（模型）：{len(recording):02d}')
                print('以下模型正在被录制：')
                for hiloModelo in recording:
                    print(f'  模型: {hiloModelo.modelo}  -->  文件: {os.path.basename(hiloModelo.file)}')
                print(f'下次检查将在 {i:02d} 秒后进行\r', end='')
                time.sleep(1)
                i -= 1
    except KeyboardInterrupt:
        print("程序被中断")
    finally:
        stop_event.set()
        for hilo in hilos:
            hilo.stop()
        for worker in postprocessingWorkers:
            worker.join()
        cleaningThread.join()
        print("所有线程已停止")

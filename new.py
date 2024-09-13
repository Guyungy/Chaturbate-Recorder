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
from concurrent.futures import ThreadPoolExecutor, as_completed

# 设置Windows控制台的模式，以支持ANSI转义字符
if os.name == 'nt':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)

# 获取脚本所在的目录
mainDir = sys.path[0]

# 配置文件解析器
Config = configparser.ConfigParser()
setting = {}

# 清屏函数，根据操作系统选择不同的命令
def cls():
    os.system('cls' if os.name == 'nt' else 'clear')

# 读取配置文件
def readConfig():
    global setting
    Config.read(mainDir + '/config.conf')

    # 从配置文件中读取各种设置
    setting = {
        'save_directory': Config.get('paths', 'save_directory'),
        'wishlist': Config.get('paths', 'wishlist'),
        'interval': int(Config.get('settings', 'checkInterval')),
        'postProcessingCommand': Config.get('settings', 'postProcessingCommand')
    }

    # 获取 postProcessingThreads，处理空值或缺少值的情况
    try:
        postProcessingThreads_str = Config.get('settings', 'postProcessingThreads').strip()
        if postProcessingThreads_str:
            setting['postProcessingThreads'] = int(postProcessingThreads_str)
        else:
            setting['postProcessingThreads'] = 1  # 默认值为1
    except (configparser.NoOptionError, ValueError):
        setting['postProcessingThreads'] = 1  # 默认值为1

    # 如果保存目录不存在，则创建目录
    os.makedirs(setting["save_directory"], exist_ok=True)

# 后处理函数，处理录制完的视频文件
def postProcess():
    while True:
        # 等待处理队列中有数据
        while processingQueue.empty():
            time.sleep(1)
        
        # 获取处理参数
        parameters = processingQueue.get()
        model = parameters['model']
        path = parameters['path']
        filename = os.path.split(path)[-1]
        directory = os.path.dirname(path)
        file = os.path.splitext(filename)[0]

        # 执行后处理命令
        subprocess.call(setting['postProcessingCommand'].split() + [path, filename, directory, model, file, 'cam4'])

# 模型线程类，负责处理模型的录制
class Modelo(threading.Thread):
    def __init__(self, modelo):
        threading.Thread.__init__(self)
        self.modelo = modelo  # 模型名称
        self._stopevent = threading.Event()  # 停止事件
        self.file = None  # 保存文件路径
        self.online = None  # 模型是否在线

    def run(self):
        isOnline = self.isOnline()  # 检查模型是否在线

        if not isOnline:
            self.online = False
        else:
            self.online = True
            self.file = os.path.join(setting['save_directory'], self.modelo, f'{datetime.datetime.fromtimestamp(time.time()).strftime("%Y.%m.%d_%H.%M.%S")}_{self.modelo}.mp4')
            
            try:
                session = streamlink.Streamlink()  # 创建Streamlink会话
                streams = session.streams(f'hlsvariant://{isOnline}')  # 获取可用的流
                stream = streams['best']  # 选择最佳质量的流
                fd = stream.open()  # 打开流

                # 检查模型是否已经在录制
                if not self.modelo in model_manager.recording:
                    os.makedirs(os.path.join(setting['save_directory'], self.modelo), exist_ok=True)
                    
                    with open(self.file, 'wb') as f:
                        model_manager.recording.add(self.modelo)  # 将模型添加到录制列表
                        
                        # 开始写入流数据到文件
                        while not (self._stopevent.isSet() or os.fstat(f.fileno()).st_nlink == 0):
                            try:
                                data = fd.read(1024)  # 从流中读取数据
                                f.write(data)  # 写入文件
                            except:
                                fd.close()  # 关闭流
                                break

                    # 如果设置了后处理命令，将任务加入处理队列
                    if setting['postProcessingCommand']:
                        processingQueue.put({'model': self.modelo, 'path': self.file})
            except Exception as e:
                # 如果发生异常，记录到日志文件
                with open('log.log', 'a+') as f:
                    f.write(f'\n{datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")} EXCEPTION: {e}\n')
                self.stop()
            finally:
                self.exceptionHandler()  # 处理异常后的清理工作

    # 异常处理函数
    def exceptionHandler(self):
        self.stop()  # 停止线程
        self.online = False  # 设置模型为不在线

        # 从录制列表中移除该模型
        model_manager.recording.remove(self.modelo)

        # 如果文件过小，删除文件
        try:
            file = os.path.join(os.getcwd(), self.file)
            if os.path.isfile(file) and os.path.getsize(file) <= 1024:
                os.remove(file)
        except Exception as e:
            with open('log.log', 'a+') as f:
                f.write(f'\n{datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")} EXCEPTION: {e}\n')

    # 检查模型是否在线
    def isOnline(self):
        try:
            resp = requests.get(f'https://chaturbate.com/api/chatvideocontext/{self.modelo}/')
            hls_url = resp.json().get('hls_source', '')
            return hls_url if hls_url else False
        except:
            return False

    # 停止线程
    def stop(self):
        self._stopevent.set()

# 封装模型线程的管理类
class ModelManager:
    def __init__(self):
        self.recording = set()  # 使用集合以提高查找和删除操作的效率

    # 添加新的模型线程
    def addModel(self, model):
        if model not in self.recording:
            thread = Modelo(model)  # 创建新线程
            thread.start()  # 启动线程

    # 清理不再活动的模型
    def cleanUp(self):
        self.recording = {model for model in self.recording if model.online}

# 添加模型线程，定期读取wishlist文件并添加新的模型线程
class AddModelsThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.wanted = []  # 保存wishlist中的模型

    def run(self):
        with open(setting['wishlist'], 'r') as f:
            lines = f.read().splitlines()
        self.wanted = list(set(x.lower() for x in lines if x))  # 去重并保存模型
        
        for model in self.wanted:
            model_manager.addModel(model)  # 添加模型线程

if __name__ == '__main__':
    readConfig()  # 读取配置文件
    model_manager = ModelManager()  # 创建模型管理器实例

    # 如果设置了后处理命令，创建线程池执行后处理
    if setting['postProcessingCommand']:
        processingQueue = queue.Queue()  # 创建处理队列
        with ThreadPoolExecutor(max_workers=setting['postProcessingThreads']) as executor:
            for _ in range(setting['postProcessingThreads']):
                executor.submit(postProcess)

    # 启动清理线程
    cleaning_thread = threading.Thread(target=model_manager.cleanUp)
    cleaning_thread.start()
    
    while True:
        try:
            readConfig()  # 重新读取配置文件
            add_models_thread = AddModelsThread()  # 创建添加模型线程
            add_models_thread.start()  # 启动添加模型线程
            
            for i in range(setting['interval'], 0, -1):
                cls()  # 清屏
                # 打印线程和模型信息
                print(f'{len(model_manager.recording):02d} active models...')
                print(f'Next check in {i:02d} seconds\r', end='')
                time.sleep(1)
            add_models_thread.join()  # 等待添加模型线程结束
        except KeyboardInterrupt:
            break

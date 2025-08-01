"""
# WebAPI文档

` python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml `

## 执行参数:
    `-a` - `绑定地址, 默认"127.0.0.1"`
    `-p` - `绑定端口, 默认9880`
    `-c` - `TTS配置文件路径, 默认"GPT_SoVITS/configs/tts_infer.yaml"`

## 调用:

### 推理

endpoint: `/tts`
GET:
```
http://127.0.0.1:9880/tts?text=先帝创业未半而中道崩殂，今天下三分，益州疲弊，此诚危急存亡之秋也。&text_lang=zh&ref_audio_path=archive_jingyuan_1.wav&prompt_lang=zh&prompt_text=我是「罗浮」云骑将军景元。不必拘谨，「将军」只是一时的身份，你称呼我景元便可&text_split_method=cut5&batch_size=1&media_type=wav&streaming_mode=true
```

POST:
```json
{
    "text": "",                   # str.(required) text to be synthesized
    "text_lang: "",               # str.(required) language of the text to be synthesized
    "ref_audio_path": "",         # str.(required) reference audio path
    "aux_ref_audio_paths": [],    # list.(optional) auxiliary reference audio paths for multi-speaker tone fusion
    "prompt_text": "",            # str.(optional) prompt text for the reference audio
    "prompt_lang": "",            # str.(required) language of the prompt text for the reference audio
    "top_k": 5,                   # int. top k sampling
    "top_p": 1,                   # float. top p sampling
    "temperature": 1,             # float. temperature for sampling
    "text_split_method": "cut0",  # str. text split method, see text_segmentation_method.py for details.
    "batch_size": 1,              # int. batch size for inference
    "batch_threshold": 0.75,      # float. threshold for batch splitting.
    "split_bucket: True,          # bool. whether to split the batch into multiple buckets.
    "speed_factor":1.0,           # float. control the speed of the synthesized audio.
    "streaming_mode": False,      # bool. whether to return a streaming response.
    "seed": -1,                   # int. random seed for reproducibility.
    "parallel_infer": True,       # bool. whether to use parallel inference.
    "repetition_penalty": 1.35    # float. repetition penalty for T2S model.
}
```

RESP:
成功: 直接返回 wav 音频流， http code 200
失败: 返回包含错误信息的 json, http code 400

### 命令控制

endpoint: `/control`

command:
"restart": 重新运行
"exit": 结束运行

GET:
```
http://127.0.0.1:9880/control?command=restart
```
POST:
```json
{
    "command": "restart"
}
```

RESP: 无


### 切换GPT模型

endpoint: `/set_gpt_weights`

GET:
```
http://127.0.0.1:9880/set_gpt_weights?weights_path=GPT_SoVITS/pretrained_models/s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt
```
RESP: 
成功: 返回"success", http code 200
失败: 返回包含错误信息的 json, http code 400


### 切换Sovits模型

endpoint: `/set_sovits_weights`

GET:
```
http://127.0.0.1:9880/set_sovits_weights?weights_path=GPT_SoVITS/pretrained_models/s2G488k.pth
```

RESP: 
成功: 返回"success", http code 200
失败: 返回包含错误信息的 json, http code 400
    
"""
import os
import sys
import traceback
from typing import Generator, Optional

now_dir = os.getcwd()
sys.path.append(now_dir)
sys.path.append("%s/GPT_SoVITS" % (now_dir))

from starlette.middleware.cors import CORSMiddleware  #引入 CORS中间件模块

#设置允许访问的域名
origins = ["*"]  #"*"，即为所有。

import config as global_config

import argparse
import subprocess
import wave
import signal
import numpy as np
import soundfile as sf
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi import FastAPI, UploadFile, File
from fastapi.staticfiles import StaticFiles
import uvicorn
from io import BytesIO
from tools.i18n.i18n import I18nAuto
from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
from GPT_SoVITS.TTS_infer_pack.text_segmentation_method import get_method_names as get_cut_method_names
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
# print(sys.path)
i18n = I18nAuto()
cut_method_names = get_cut_method_names()

parser = argparse.ArgumentParser(description="GPT-SoVITS api")
parser.add_argument("-c", "--tts_config", type=str, default="GPT_SoVITS/configs/tts_infer.yaml", help="tts_infer路径")
parser.add_argument("-a", "--bind_addr", type=str, default="127.0.0.1", help="default: 127.0.0.1")
parser.add_argument("-p", "--port", type=int, default=9880, help="default: 9880")
args = parser.parse_args()
config_path = args.tts_config
# device = args.device
port = args.port
host = args.bind_addr
argv = sys.argv

if config_path in [None, ""]:
    config_path = "GPT-SoVITS/configs/tts_infer.yaml"

tts_config = TTS_Config(config_path)
print(tts_config)
tts_pipeline = TTS(tts_config)

APP = FastAPI()


APP.mount("/srt", StaticFiles(directory="音频输出"), name="音频输出")

APP.add_middleware(
    CORSMiddleware, 
    allow_origins=origins,  #设置允许的origins来源
    allow_credentials=True,
    allow_methods=["*"],  # 设置允许跨域的http方法，比如 get、post、put等。
    allow_headers=["*"])  #允许跨域的headers，可以用来鉴别来源等作用。


class TTS_Request(BaseModel):
    text: str = None
    text_lang: str = "zh"
    ref_audio_path: str = None
    aux_ref_audio_paths: list = None
    prompt_lang: str = "zh"
    prompt_text: str = ""
    top_k:int = 5
    top_p:float = 1
    temperature:float = 1
    text_split_method:str = "cut5"
    batch_size:int = 1
    batch_threshold:float = 0.75
    split_bucket:bool = True
    speed_factor:float = 1.0
    fragment_interval:float = 0.3
    seed:int = -1
    media_type:str = "wav"
    streaming_mode:bool = False
    parallel_infer:bool = True
    repetition_penalty:float = 1.35

### modify from https://github.com/RVC-Boss/GPT-SoVITS/pull/894/files
def pack_ogg(io_buffer:BytesIO, data:np.ndarray, rate:int):
    with sf.SoundFile(io_buffer, mode='w', samplerate=rate, channels=1, format='ogg') as audio_file:
        audio_file.write(data)
    return io_buffer

def replace_speaker(text):
    import re
    return re.sub(r"\[.*?\]", "", text, flags=re.UNICODE)


def pack_raw(io_buffer:BytesIO, data:np.ndarray, rate:int):
    io_buffer.write(data.tobytes())
    return io_buffer


def pack_wav(io_buffer:BytesIO, data:np.ndarray, rate:int):
    io_buffer = BytesIO()
    sf.write(io_buffer, data, rate, format='wav')
    return io_buffer

def pack_aac(io_buffer:BytesIO, data:np.ndarray, rate:int):
    process = subprocess.Popen([
        'ffmpeg',
        '-f', 's16le',  # 输入16位有符号小端整数PCM
        '-ar', str(rate),  # 设置采样率
        '-ac', '1',  # 单声道
        '-i', 'pipe:0',  # 从管道读取输入
        '-c:a', 'aac',  # 音频编码器为AAC
        '-b:a', '192k',  # 比特率
        '-vn',  # 不包含视频
        '-f', 'adts',  # 输出AAC数据流格式
        'pipe:1'  # 将输出写入管道
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _ = process.communicate(input=data.tobytes())
    io_buffer.write(out)
    return io_buffer

def pack_audio(io_buffer:BytesIO, data:np.ndarray, rate:int, media_type:str):
    if media_type == "ogg":
        io_buffer = pack_ogg(io_buffer, data, rate)
    elif media_type == "aac":
        io_buffer = pack_aac(io_buffer, data, rate)
    elif media_type == "wav":
        io_buffer = pack_wav(io_buffer, data, rate)
    else:
        io_buffer = pack_raw(io_buffer, data, rate)
    io_buffer.seek(0)
    return io_buffer



# from https://huggingface.co/spaces/coqui/voice-chat-with-mistral/blob/main/app.py
def wave_header_chunk(frame_input=b"", channels=1, sample_width=2, sample_rate=32000):
    # This will create a wave header then append the frame input
    # It should be first on a streaming wav file
    # Other frames better should not have it (else you will hear some artifacts each chunk start)
    wav_buf = BytesIO()
    with wave.open(wav_buf, "wb") as vfout:
        vfout.setnchannels(channels)
        vfout.setsampwidth(sample_width)
        vfout.setframerate(sample_rate)
        vfout.writeframes(frame_input)

    wav_buf.seek(0)
    return wav_buf.read()


def handle_control(command:str):
    if command == "restart":
        os.execl(sys.executable, sys.executable, *argv)
    elif command == "exit":
        os.kill(os.getpid(), signal.SIGTERM)
        exit(0)


def check_illegal_chars(text: str) -> tuple[bool, str]:
    """检查文本中是否包含非法字符（只允许各种语言文字、字母、数字和标点符号）
    
    Args:
        text: 要检查的文本
        
    Returns:
        (is_valid, message): 是否合法及错误信息
    """
    import re
    # 匹配所有常见语言文字、字母、数字、标点符号
    pattern = (
        r'['
        r'\u0000-\u007f'  # 基本拉丁字母（ASCII）
        r'\u0080-\u00ff'  # 拉丁文补充1
        r'\u0100-\u017f'  # 拉丁文扩展A
        r'\u0180-\u024f'  # 拉丁文扩展B
        r'\u0250-\u02af'  # 国际音标扩展
        r'\u0370-\u03ff'  # 希腊语和科普特语
        r'\u0400-\u04ff'  # 西里尔字母
        r'\u0500-\u052f'  # 西里尔字母补充
        r'\u0530-\u058f'  # 亚美尼亚语
        r'\u0590-\u05ff'  # 希伯来语
        r'\u0600-\u06ff'  # 阿拉伯语
        r'\u0750-\u077f'  # 阿拉伯语补充
        r'\u0900-\u097f'  # 天城文
        r'\u0980-\u09ff'  # 孟加拉语
        r'\u0a00-\u0a7f'  # 果鲁穆奇语
        r'\u0e00-\u0e7f'  # 泰语
        r'\u1000-\u109f'  # 缅甸语
        r'\u1100-\u11ff'  # 谚文字母
        r'\u3040-\u309f'  # 平假名
        r'\u30a0-\u30ff'  # 片假名
        r'\u31f0-\u31ff'  # 片假名音标扩展
        r'\u3200-\u32ff'  # 带圈字符
        r'\u3300-\u33ff'  # CJK兼容
        r'\u3400-\u4dbf'  # CJK统一表意文字扩展A
        r'\u4e00-\u9fff'  # CJK统一表意文字
        r'\uac00-\ud7af'  # 谚文音节
        r'\uf900-\ufaff'  # CJK兼容表意文字
        r'\uff00-\uffef'  # 全角ASCII、全角标点
        r'\U00020000-\U0002a6df'  # CJK统一表意文字扩展B
        r'\u2000-\u206f'  # 常用标点
        # r'\u2070-\u209f'  # 上标和下标
        r'\u20a0-\u20cf'  # 货币符号
        r'\u20d0-\u20ff'  # 组合记号
        r'\u2100-\u214f'  # 字母式符号
        r'\u2150-\u218f'  # 数字形式
        # r'\u2190-\u21ff'  # 箭头
        r'\u2200-\u22ff'  # 数学运算符
        # r'\u2300-\u23ff'  # 杂项工业符号
        # r'\u2400-\u243f'  # 控制图片
        # r'\u2440-\u245f'  # OCR
        # r'\u2460-\u24ff'  # 带圈或括号的字母数字
        r'\u2500-\u257f'  # 制表符
        # r'\u2580-\u259f'  # 方块元素
        # r'\u25a0-\u25ff'  # 几何图形
        # r'\u2600-\u26ff'  # 杂项符号
        # r'\u2700-\u27bf'  # 装饰符号
        r'\u3000-\u303f'  # CJK符号和标点
        r'\s'  # 所有空白字符（包括空格、制表符、换行符等）
        r']+'
    )
    # 检查是否所有字符都匹配模式
    if all(re.match(pattern, char) for char in text):
        return True, ""
    # 找出第一个不匹配的字符
    for i, char in enumerate(text):
        if not re.match(pattern, char):
            return False, f"text contains illegal character '{char}' at position {i}: {text}"
    return True, ""

def check_params(req:dict):
    text:str = req.get("text", "")
    text_lang:str = req.get("text_lang", "")
    ref_audio_path:str = req.get("ref_audio_path", "")
    streaming_mode:bool = req.get("streaming_mode", False)
    media_type:str = req.get("media_type", "wav")
    prompt_lang:str = req.get("prompt_lang", "")
    text_split_method:str = req.get("text_split_method", "cut5")
    prompt_text:str = req.get("prompt_text", "")

    if ref_audio_path in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "ref_audio_path is required"})
    if text in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "text is required"})
        
    # 检查文本中的非法字符
    is_valid, error_msg = check_illegal_chars(text)
    if not is_valid:
        return JSONResponse(status_code=400, content={"message": error_msg})
    if prompt_text:
        is_valid, error_msg = check_illegal_chars(prompt_text)
        if not is_valid:
            return JSONResponse(status_code=400, content={"message": f"prompt_text {error_msg}"})
            
    if (text_lang in [None, ""]) :
        return JSONResponse(status_code=400, content={"message": "text_lang is required"})
    elif text_lang.lower() not in tts_config.languages:
        return JSONResponse(status_code=400, content={"message": f"text_lang: {text_lang} is not supported in version {tts_config.version}"})
    if (prompt_lang in [None, ""]) :
        return JSONResponse(status_code=400, content={"message": "prompt_lang is required"})
    elif prompt_lang.lower() not in tts_config.languages:
        return JSONResponse(status_code=400, content={"message": f"prompt_lang: {prompt_lang} is not supported in version {tts_config.version}"})
    if media_type not in ["wav", "raw", "ogg", "aac"]:
        return JSONResponse(status_code=400, content={"message": f"media_type: {media_type} is not supported"})
    elif media_type == "ogg" and  not streaming_mode:
        return JSONResponse(status_code=400, content={"message": "ogg format is not supported in non-streaming mode"})
    
    if text_split_method not in cut_method_names:
        return JSONResponse(status_code=400, content={"message": f"text_split_method:{text_split_method} is not supported"})

    return None

async def tts_handle(req:dict):
    """
    Text to speech handler.
    
    Args:
        req (dict): 
            {
                "text": "",                   # str.(required) text to be synthesized
                "text_lang: "",               # str.(required) language of the text to be synthesized
                "ref_audio_path": "",         # str.(required) reference audio path
                "aux_ref_audio_paths": [],    # list.(optional) auxiliary reference audio paths for multi-speaker synthesis
                "prompt_text": "",            # str.(optional) prompt text for the reference audio
                "prompt_lang": "",            # str.(required) language of the prompt text for the reference audio
                "top_k": 5,                   # int. top k sampling
                "top_p": 1,                   # float. top p sampling
                "temperature": 1,             # float. temperature for sampling
                "text_split_method": "cut5",  # str. text split method, see text_segmentation_method.py for details.
                "batch_size": 1,              # int. batch size for inference
                "batch_threshold": 0.75,      # float. threshold for batch splitting.
                "split_bucket: True,          # bool. whether to split the batch into multiple buckets.
                "speed_factor":1.0,           # float. control the speed of the synthesized audio.
                "fragment_interval":0.3,      # float. to control the interval of the audio fragment.
                "seed": -1,                   # int. random seed for reproducibility.
                "media_type": "wav",          # str. media type of the output audio, support "wav", "raw", "ogg", "aac".
                "streaming_mode": False,      # bool. whether to return a streaming response.
                "parallel_infer": True,       # bool.(optional) whether to use parallel inference.
                "repetition_penalty": 1.35    # float.(optional) repetition penalty for T2S model.          
            }
    returns:
        StreamingResponse: audio stream response.
    """
    try:
        streaming_mode = req.get("streaming_mode", False)
        return_fragment = req.get("return_fragment", False)
        media_type = req.get("media_type", "wav")

        check_res = check_params(req)
        if check_res is not None:
            return check_res

        if streaming_mode or return_fragment:
            req["return_fragment"] = True
        
        
        tts_generator = tts_pipeline.run(req)

        if streaming_mode:
            def streaming_generator(tts_generator: Generator, media_type: str):
                if media_type == "wav":
                    yield wave_header_chunk()
                    media_type = "raw"
                for sr, chunk in tts_generator:
                    yield pack_audio(BytesIO(), chunk, sr, media_type).getvalue()
            
            return StreamingResponse(
                streaming_generator(tts_generator, media_type), 
                media_type=f"audio/{media_type}"
            )
        else:
            sr, audio_data = next(tts_generator)
            audio_data = pack_audio(BytesIO(), audio_data, sr, media_type).getvalue()
            return Response(audio_data, media_type=f"audio/{media_type}")

    except Exception as e:
        import traceback
        
        print(traceback.format_exc())
        return JSONResponse(
            status_code=400,
            content={
                "message": "tts failed",
                "Exception": str(e)
            }
        )





async def tts_handle_srt(req:dict,request):
    """
    Text to speech handler.
    
    Args:
        req (dict): 
            {
                "text": "",                   # str.(required) text to be synthesized
                "text_lang: "",               # str.(required) language of the text to be synthesized
                "ref_audio_path": "",         # str.(required) reference audio path
                "prompt_text": "",            # str.(optional) prompt text for the reference audio
                "prompt_lang": "",            # str.(required) language of the prompt text for the reference audio
                "top_k": 5,                   # int. top k sampling
                "top_p": 1,                   # float. top p sampling
                "temperature": 1,             # float. temperature for sampling
                "text_split_method": "cut5",  # str. text split method, see text_segmentation_method.py for details.
                "batch_size": 1,              # int. batch size for inference
                "batch_threshold": 0.75,      # float. threshold for batch splitting.
                "split_bucket: True,          # bool. whether to split the batch into multiple buckets.
                "speed_factor":1.0,           # float. control the speed of the synthesized audio.
                "fragment_interval":0.3,      # float. to control the interval of the audio fragment.
                "seed": -1,                   # int. random seed for reproducibility.
                "media_type": "wav",          # str. media type of the output audio, support "wav", "raw", "ogg", "aac".
                "streaming_mode": False,      # bool. whether to return a streaming response.
                "parallel_infer": True,       # bool.(optional) whether to use parallel inference.
                "repetition_penalty": 1.35    # float.(optional) repetition penalty for T2S model.          
            }
    returns:
        StreamingResponse: audio stream response.
    """
    try:
        streaming_mode = req.get("streaming_mode", False)
        media_type = req.get("media_type", "wav")

        check_res = check_params(req)
        if check_res is not None:
            return check_res

    
    
        tts_generator=tts_pipeline.run(req)
        
        sr, audio_data = next(tts_generator)
        print(audio_data)
        #audio_data = pack_audio(BytesIO(), audio_data, sr, media_type).getvalue()
        #return Response(audio_data, media_type=f"audio/{media_type}")
        return JSONResponse({"code":"200", "srt":f"http://{request.url.hostname}:{request.url.port}/srt/tts-out.srt","audio":f"http://{request.url.hostname}:{request.url.port}/srt/audio.wav"})
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": f"tts failed", "Exception": str(e)})

@APP.get("/control")
async def control(command: str = None):
    if command is None:
        return JSONResponse(status_code=400, content={"message": "command is required"})
    handle_control(command)


@APP.get("/srt")
async def tts_get_endpoint_srt(request: Request,
                        text: str = None,
                        text_lang: str = None,
                        ref_audio_path: str = None,
                        prompt_lang: str = None,
                        prompt_text: str = "",
                        top_k:int = 5,
                        top_p:float = 1,
                        temperature:float = 1,
                        text_split_method:str = "cut5",
                        batch_size:int = 10,
                        batch_threshold:float = 0.75,
                        split_bucket:bool = True,
                        speed_factor:float = 1.0,
                        fragment_interval:float = 0.3,
                        seed:int = -1,
                        media_type:str = "wav",
                        streaming_mode:bool = False,
                        parallel_infer:bool = True,
                        repetition_penalty:float = 1.35
                        ):
    req = {
        "text": text,
        "text_lang": text_lang.lower(),
        "ref_audio_path": ref_audio_path,
        "prompt_text": prompt_text,
        "prompt_lang": prompt_lang.lower(),
        "top_k": top_k,
        "top_p": top_p,
        "temperature": temperature,
        "text_split_method": text_split_method,
        "batch_size":int(batch_size),
        "batch_threshold":float(batch_threshold),
        "speed_factor":float(speed_factor),
        "split_bucket":split_bucket,
        "fragment_interval":fragment_interval,
        "seed":seed,
        "media_type":media_type,
        "streaming_mode":streaming_mode,
        "parallel_infer":parallel_infer,
        "repetition_penalty":float(repetition_penalty)
    }
    return await tts_handle_srt(req,request)

@APP.post("/srt")
async def tts_post_endpoint_srt(request: TTS_Request,req1: Request):
    req = request.dict()
    return await tts_handle_srt(req,req1)



@APP.get("/")
async def tts_get_endpoint(
    text: str = None,
    text_lang: str = "zh",
    ref_audio_path: str = None,
    aux_ref_audio_paths:list = None,
    prompt_lang: str = "zh",
    prompt_text: str = "",
    top_k:int = 5,
    top_p:float = 1,
    temperature:float = 1,
    text_split_method:str = "cut5",
    batch_size:int = 1,
    batch_threshold:float = 0.75,
    split_bucket:bool = True,
    speed_factor:float = 1.0,
    fragment_interval:float = 0.3,
    seed:int = -1,
    media_type:str = "wav",
    streaming_mode:bool = False,
    parallel_infer:bool = True,
    repetition_penalty:float = 1.35
):
    req = {
        "text": text,
        "text_lang": text_lang.lower(),
        "ref_audio_path": ref_audio_path,
        "aux_ref_audio_paths": aux_ref_audio_paths,
        "prompt_text": prompt_text,
        "prompt_lang": prompt_lang.lower(),
        "top_k": top_k,
        "top_p": top_p,
        "temperature": temperature,
        "text_split_method": text_split_method,
        "batch_size":int(batch_size),
        "batch_threshold":float(batch_threshold),
        "speed_factor":float(speed_factor),
        "split_bucket":split_bucket,
        "fragment_interval":fragment_interval,
        "seed":seed,
        "media_type":media_type,
        "streaming_mode":streaming_mode,
        "parallel_infer":parallel_infer,
        "repetition_penalty":float(repetition_penalty)
    }
    return await tts_handle(req)
                

@APP.post("/")
async def tts_post_endpoint(request: TTS_Request):
    req = request.dict()
    return await tts_handle(req)

@APP.get("/tts")
async def tts_get_endpoint(
    text: str = None,
    text_lang: str = "zh",
    ref_audio_path: str = None,
    aux_ref_audio_paths:list = None,
    prompt_lang: str = "zh",
    prompt_text: str = "",
    top_k:int = 5,
    top_p:float = 1,
    temperature:float = 1,
    text_split_method:str = "cut5",
    batch_size:int = 1,
    batch_threshold:float = 0.75,
    split_bucket:bool = True,
    speed_factor:float = 1.0,
    fragment_interval:float = 0.3,
    seed:int = -1,
    media_type:str = "wav",
    streaming_mode:bool = False,
    parallel_infer:bool = True,
    repetition_penalty:float = 1.35
):
    req = {
        "text": text,
        "text_lang": text_lang.lower(),
        "ref_audio_path": ref_audio_path,
        "aux_ref_audio_paths": aux_ref_audio_paths,
        "prompt_text": prompt_text,
        "prompt_lang": prompt_lang.lower(),
        "top_k": top_k,
        "top_p": top_p,
        "temperature": temperature,
        "text_split_method": text_split_method,
        "batch_size":int(batch_size),
        "batch_threshold":float(batch_threshold),
        "speed_factor":float(speed_factor),
        "split_bucket":split_bucket,
        "fragment_interval":fragment_interval,
        "seed":seed,
        "media_type":media_type,
        "streaming_mode":streaming_mode,
        "parallel_infer":parallel_infer,
        "repetition_penalty":float(repetition_penalty)
    }
    return await tts_handle(req)
                

@APP.post("/tts")
async def tts_post_endpoint(request: TTS_Request):
    req = request.dict()
    return await tts_handle(req)

@APP.get("/set_refer_audio")
async def set_refer_aduio(refer_audio_path: str = None):
    try:
        tts_pipeline.set_ref_audio(refer_audio_path)
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": f"set refer audio failed", "Exception": str(e)})
    return JSONResponse(status_code=200, content={"message": "success"})


# @APP.post("/set_refer_audio")
# async def set_refer_aduio_post(audio_file: UploadFile = File(...)):
#     try:
#         # 检查文件类型，确保是音频文件
#         if not audio_file.content_type.startswith("audio/"):
#             return JSONResponse(status_code=400, content={"message": "file type is not supported"})
        
#         os.makedirs("uploaded_audio", exist_ok=True)
#         save_path = os.path.join("uploaded_audio", audio_file.filename)
#         # 保存音频文件到服务器上的一个目录
#         with open(save_path , "wb") as buffer:
#             buffer.write(await audio_file.read())
            
#         tts_pipeline.set_ref_audio(save_path)
#     except Exception as e:
#         return JSONResponse(status_code=400, content={"message": f"set refer audio failed", "Exception": str(e)})
#     return JSONResponse(status_code=200, content={"message": "success"})

@APP.get("/set_gpt_weights")
async def set_gpt_weights(weights_path: str = None):
    try:
        if weights_path in ["", None]:
            return JSONResponse(status_code=400, content={"message": "gpt weight path is required"})
        tts_pipeline.init_t2s_weights(weights_path)
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": f"change gpt weight failed", "Exception": str(e)})

    return JSONResponse(status_code=200, content={"message": "success"})


@APP.get("/set_sovits_weights")
async def set_sovits_weights(weights_path: str = None):
    try:
        if weights_path in ["", None]:
            return JSONResponse(status_code=400, content={"message": "sovits weight path is required"})
        tts_pipeline.init_vits_weights(weights_path)
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": f"change sovits weight failed", "Exception": str(e)})
    return JSONResponse(status_code=200, content={"message": "success"})

@APP.post("/set_model")
async def set_model(request: Request):
    try:
        json_post_raw = await request.json()
        gpt_path = json_post_raw.get("gpt_model_path")
        sovits_path = json_post_raw.get("sovits_model_path")

        if not gpt_path or not sovits_path:
            return JSONResponse(
                status_code=400, 
                content={
                    "message": "Both gpt_model_path and sovits_model_path are required",
                    "status": "error"
                }
            )

        # 加载 GPT 模型
        try:
            tts_pipeline.init_t2s_weights(gpt_path)
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={
                    "message": f"Failed to load GPT model: {str(e)}",
                    "status": "error"
                }
            )

        # 加载 SoVITS 模型
        try:
            tts_pipeline.init_vits_weights(sovits_path)
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={
                    "message": f"Failed to load SoVITS model: {str(e)}",
                    "status": "error"
                }
            )

        return JSONResponse(
            status_code=200,
            content={
                "message": "Successfully loaded both models",
                "status": "success",
                "gpt_model": gpt_path,
                "sovits_model": sovits_path
            }
        )

    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={
                "message": f"Error processing request: {str(e)}",
                "status": "error"
            }
        )

@APP.get("/speakers")
def speakers_endpoint():

    voices = []

    for name in os.listdir("参考音频"):
        name = name.replace(".wav","").replace(".mp3","").replace(".WAV","")
        voices.append({"name":name,"voice_id":name})

    return JSONResponse(voices, status_code=200)


@APP.get("/speakers_list")
def speakerlist_endpoint():
    return JSONResponse(["female_calm","female","male"], status_code=200)

@APP.get("/list_gpt_models")
async def list_gpt_models():
    try:
        gpt_dir = "GPT_weights_v2"
        
        if not os.path.exists(gpt_dir):
            return JSONResponse(
                status_code=200,
                content={
                    "models": [],
                    "message": "GPT models directory not found"
                }
            )

        # 获取所有.ckpt文件
        model_files = []
        for file in os.listdir(gpt_dir):
            if file.endswith(".ckpt"):
                # 使用相对路径
                rel_path = os.path.join(gpt_dir, file)
                # 统一使用正斜杠
                rel_path = rel_path.replace("\\", "/")
                model_files.append(rel_path)

        return JSONResponse(
            status_code=200,
            content={
                "models": model_files,
                "message": "success"
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "models": [],
                "message": f"Error listing GPT models: {str(e)}"
            }
        )

@APP.get("/list_sovits_models")
async def list_sovits_models():
    try:
        sovits_dir = "SoVITS_weights_v2"
        
        if not os.path.exists(sovits_dir):
            return JSONResponse(
                status_code=200,
                content={
                    "models": [],
                    "message": "SoVITS models directory not found"
                }
            )

        # 获取所有.pth文件
        model_files = []
        for file in os.listdir(sovits_dir):
            if file.endswith(".pth"):
                # 使用相对路径
                rel_path = os.path.join(sovits_dir, file)
                # 统一使用正斜杠
                rel_path = rel_path.replace("\\", "/")
                model_files.append(rel_path)

        return JSONResponse(
            status_code=200,
            content={
                "models": model_files,
                "message": "success"
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "models": [],
                "message": f"Error listing SoVITS models: {str(e)}"
            }
        )

@APP.post("/tts_to_audio/")
async def tts_to_audio(request: TTS_Request):
    req = request.dict()
    # "text": "",                   # str.(required) text to be synthesized
    # "text_lang": "",              # str.(required) language of the text to be synthesized
    # "ref_audio_path": "",         # str.(required) reference audio path.
    # "prompt_text": "",            # str.(optional) prompt text for the reference audio
    # "prompt_lang": "", 
    req["text_lang"] = global_config.llama_lang
    req["ref_audio_path"] = global_config.llama_audio
    req["prompt_text"] = global_config.llama_text
    req["prompt_lang"] = global_config.llama_prompt_lang
    req["batch_size"] = 10
    return await tts_handle(req)

def graceful_exit(signum, frame):
    print("exit...")
    os.kill(os.getpid(), signal.SIGTERM)  # 发送终止信号
    exit(0)

if __name__ == "__main__":
    try:
        signal.signal(signal.SIGTERM, graceful_exit)
        signal.signal(signal.SIGINT, graceful_exit)  
        uvicorn.run(app="api_v2:APP", host="0.0.0.0", port=port, workers=1)
    except Exception as e:
        traceback.print_exc()
        os.kill(os.getpid(), signal.SIGTERM)
        exit(0)

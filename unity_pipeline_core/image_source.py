"""Preserve Photoshop's first alpha channel when Blender drops it on import.

Reads the merged image only; it does not flatten or modify the source document.
Format reference: https://www.adobe.com/devnet-apps/photoshop/fileformatashtml/
"""
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import struct
import tempfile
import zlib

VERSION = 1


def _read(stream, count):
    data = stream.read(count)
    if len(data) != count:
        raise ValueError('Truncated Photoshop image')
    return data


def _packbits(data, expected):
    result = bytearray()
    i = 0
    while i < len(data):
        count = data[i]; i += 1
        if count < 128:
            size = count + 1
            if i + size > len(data):raise ValueError('Truncated PSD RLE literal')
            result.extend(data[i:i+size]); i += size
        elif count > 128:
            if i == len(data):raise ValueError('Truncated PSD RLE repeat')
            result.extend(data[i:i+1] * (257-count)); i += 1
        if len(result) > expected:raise ValueError('Invalid PSD row size')
    if len(result) != expected:raise ValueError('Invalid PSD row size')
    return result


def read_psd(path):
    with Path(path).open('rb') as stream:
        magic, version, _, channels, height, width, depth, mode = struct.unpack('>4sH6sHIIHH', _read(stream,26))
        if magic != b'8BPS' or version != 1 or mode not in (1,3) or depth not in (8,16):
            raise ValueError('PSD preview supports RGB/grayscale 8/16-bit PSD; export other formats as RGBA PNG/TIFF')
        colors = 3 if mode == 3 else 1
        if channels < colors or not width or not height or channels*width*height*(depth//8) > 512*1024*1024:
            raise ValueError('Invalid or oversized PSD preview')
        for _ in range(3):
            stream.seek(struct.unpack('>I',_read(stream,4))[0],1)
        compression = struct.unpack('>H',_read(stream,2))[0]
        row_bytes = width*(depth//8)
        plane_bytes = row_bytes*height
        if compression == 0:
            planes = [_read(stream,plane_bytes) for _ in range(channels)]
        elif compression == 1:
            counts = struct.unpack('>'+str(channels*height)+'H',_read(stream,channels*height*2))
            planes = [b''.join(_packbits(_read(stream,counts[c*height+y]),row_bytes) for y in range(height)) for c in range(channels)]
        elif compression in (2,3):
            data = zlib.decompress(stream.read())
            if len(data) != channels*plane_bytes:raise ValueError('Invalid PSD ZIP size')
            if compression == 3:
                if depth != 8:raise ValueError('16-bit PSD ZIP prediction is not supported; save with RLE or as PNG')
                data = bytearray(data)
                for start in range(0,len(data),row_bytes):
                    for x in range(1,row_bytes):data[start+x]=(data[start+x]+data[start+x-1])&255
            planes = [data[c*plane_bytes:(c+1)*plane_bytes] for c in range(channels)]
        else:raise ValueError('Unsupported PSD compression')
    rgb = planes[:3] if colors == 3 else [planes[0]]*3
    alpha = planes[colors] if channels > colors else bytes([255])*plane_bytes
    return width,height,depth,rgb+[alpha]


def _chunk(name, data):
    return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data)&0xffffffff)


def encode_png(width, height, depth, planes):
    stride=depth//8; row_bytes=width*stride
    interleaved=bytearray(width*height*4*stride)
    for c in range(4):
        for b in range(stride):interleaved[c*stride+b::4*stride]=planes[c][b::stride]
    rows=b''.join(b'\0'+interleaved[y*row_bytes*4:(y+1)*row_bytes*4] for y in range(height))
    return b'\x89PNG\r\n\x1a\n'+_chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,depth,6,0,0,0))+_chunk(b'IDAT',zlib.compress(rows))+_chunk(b'IEND',b'')


def resolve(path, cache_directory=None):
    source=Path(path)
    if source.suffix.lower() != '.psd':return str(source)
    stamp=source.stat()
    key=sha256(f'{VERSION}|{source.resolve()}|{stamp.st_size}|{stamp.st_mtime_ns}'.encode()).hexdigest()
    cache=Path(cache_directory) if cache_directory else Path(os.environ.get('LOCALAPPDATA',tempfile.gettempdir()))/'UnityMaterialPipeline'/'texture-cache'
    target=cache/(source.stem+'-'+key[:20]+'.png')
    if not target.is_file():
        payload=encode_png(*read_psd(source));cache.mkdir(parents=True,exist_ok=True)
        descriptor,temporary=tempfile.mkstemp(dir=cache,suffix='.tmp')
        try:
            with os.fdopen(descriptor,'wb') as stream:stream.write(payload)
            os.replace(temporary,target)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)
    return str(target)


def shader_images(document):
    if not document:return document
    textures=document.get('material',{}).get('textures',{})
    replacements={name:resolve(entry['absolute_path']) for name,entry in textures.items() if entry.get('absolute_path') and Path(entry['absolute_path']).suffix.lower()=='.psd'}
    if not replacements:return document
    result=dict(document);result['material']=dict(document['material']);result['material']['textures']=deepcopy(textures)
    for name,path in replacements.items():result['material']['textures'][name]['absolute_path']=path
    return result

#Cheatsheet
# print(x) can show a hex as a number
#hex(x) can show a number as a hex
#divmod(x, y) will give both quotient and remainder
#0xFEFF is stored as FF FE, which tells readers “this file is little-endian.” 
# < is little edian B is x00 H is x0000 I is x00000000
#https://www.3dbrew.org/wiki/BCSTM for more information of the actual BCSTM file

# Libraries
import array, math, wave, struct, os, subprocess, tempfile 
from pathlib import Path

def align(value, boundary=0x20):
    remainder = value % boundary
    if remainder == 0:
        return value
    return value + (boundary-remainder)

def pad_to(data, boundary=0x20):
    target = align(len(data), boundary)    # the length we want to end up with
    missing = target - len(data)             # how many bytes short are we?
    return data + bytes(missing)

#reference = type + offset H=type H=padding I=offset
def reference(type_id, offset):
    return struct.pack('<HHI', type_id,0,offset)
    

#sized reference = type + offset + bytes H=type H=padding I=offset I=bytes
def sized_reference(type_id, offset, size):
    return reference(type_id, offset) + struct.pack('<I',size)

def split_channels(raw, channel_count):
    samples = array.array('h')
    samples.frombytes(raw)
    result = []
    for c in range(channel_count):
        result.append(samples[c::channel_count].tobytes())
    return result

def read_wav(path):
    with wave.open(path, 'rb') as w:
        sample_rate = w.getframerate()
        frame_count = w.getnframes()
        channel_count = w.getnchannels()
        sample_width = w.getsampwidth()
        
        if sample_width != 2:
            raise ValueError("Expected 16-bit audio.")
        if frame_count == 0:
            raise ValueError("File has no audio.")

        raw = w.readframes(frame_count)

    channels = split_channels(raw, channel_count)
    return sample_rate, frame_count, channels

def compute_layout(frame_count):
    block_samples = 4096
    block_size = 8192
    full, rest = divmod(frame_count, block_samples)
    if rest == 0:
        block_count = full
        last_samples = 4096
    else:
        block_count = full + 1
        last_samples = rest
    last_size = last_samples*2
    last_padded_size = align(last_size)
    return{
        'block_count'      : block_count,
        'block_size'       : block_size,
        'block_samples'    : block_samples,  
        'last_samples'     : last_samples,
        'last_size'        : last_size,
        'last_padded_size' : last_padded_size
    }

## DATA BLOCK

def build_data_block(channels,layout):
    # definitions
    block_count = layout['block_count']
    block_size = layout['block_size']
    last_size = layout['last_size']
    last_padded_size = layout['last_padded_size']

    # error check channels
    if not channels:
        raise ValueError("No audio channels found.")
   
    expected_channel_bytes=((block_count-1)*block_size+last_size)
    for i, channel in enumerate(channels):
        if len(channel) != expected_channel_bytes:
            raise ValueError(
                f"Channel {i} has {len(channel)} bytes; "
                f"expected {expected_channel_bytes}"
            )
    # main loop
    payload = bytearray()

    for b in range(block_count):
        is_last = b == block_count - 1
        start = b * block_size # This is where the block beings for each channel

        if is_last:
            actual_size = last_size
            stored_size = last_padded_size
        else:
            actual_size = block_size
            stored_size = block_size

        for ch in channels:
            piece = ch[start:start + actual_size]

            payload.extend(piece)
            payload.extend(bytes(stored_size - actual_size))

    data = bytearray(0x20)
    data[0:4] = b'DATA'
    data.extend(payload)

    struct.pack_into('<I', data, 4, len(data))
    offset = 0x20 - 0x08
    return bytes(data), offset

## SEEK BLOCK

def build_seek_block(payload = b''):
    unpadded_size = 0x08 + len(payload)
    block_size = align(unpadded_size, 0x20)

    # creates a block of zeros to fill
    seek = bytearray(block_size)
    seek[0:4] = b'SEEK'

    # move seek into our byte array
    struct.pack_into('<I', seek, 4, block_size)

    #grab seek data into payload
    seek[0x08:0x08 + len(payload)] = payload
    return bytes(seek)

## INFO BLOCK


def build_stream_info(sample_rate, frame_count, channel_count, layout, sample_offset):
    header = struct.pack('<BBBB', 1, 0, channel_count, 0)
    stream_info =  header + (
        struct.pack('<IIIIIIIIIII', sample_rate, 0, frame_count, layout['block_count'],
        layout['block_size'], layout['block_samples'], layout['last_size'], 
        layout['last_samples'], layout['last_padded_size'], 0, 0)
        + reference(0x1F00, sample_offset)
    )
    return stream_info

def build_track_info(channel_count):
    header = (struct.pack('<BBH',0x7f, 0x40, 0) + reference(0x0100,0x0C))

    channel_indexes = bytes(range(channel_count))
    byte_table = struct.pack('<I', channel_count) + channel_indexes

    byte_table = pad_to(byte_table, 4)

    track_info = header + byte_table

    return track_info

def build_channel_info(channel_count): return reference(0, 0xFFFFFFFF) * channel_count
    
# Find proper byte positions for writing 
# Ref table helper function
def reference_table_size(count):
    return 4+ count * 8

def compute_info_positions(channel_count):
    stream        = 0x20
    stream_size   = 0x38

    track_table = stream + stream_size
    track_table_size = reference_table_size(1)

    channel_table = track_table + track_table_size
    channel_table_size = reference_table_size(channel_count)

    track_info    = channel_table + channel_table_size
    byte_table_size = align(4 + channel_count, 4)
    track_info_size = 0x0C + byte_table_size
    

    channel_info  = track_info + track_info_size
    channel_info_size = channel_count * 8

    end           = channel_info + channel_info_size
    info_size = align(end, 0x20)
    
    return {
        'stream': stream,
        'stream_size': stream_size,
        'track_table': track_table,
        'track_table_size': track_table_size,
        'channel_table': channel_table,
        'channel_table_size': channel_table_size,
        'track_info': track_info,
        'track_info_size': track_info_size,
        'channel_info': channel_info,
        'channel_info_size': channel_info_size,
        'end': end,
        'info_size': info_size,
    }

def build_info_block(sample_rate, frame_count, channel_count, layout, sample_offset):
    # Setup positions
    pos = compute_info_positions(channel_count)

    # build stream info, track tables, track info, and channel info
    
    ## STREAM INFO
    stream_info = build_stream_info(
        sample_rate, 
        frame_count, 
        channel_count, 
        layout, 
        sample_offset
    )
    # verify
    if len(stream_info) != pos['stream_size']:
        raise ValueError("Stream Info has an unexpected size.")
    
    ## TRACK TABLE
    track_table = (struct.pack('<I', 1)+ reference(0x4101, pos['track_info'] - pos['track_table']))
    #verify
    if len(track_table) != pos['track_table_size']:
        raise ValueError("Track Table has an unexpected size.")

    ## TRACK INFO
    track_info = build_track_info(channel_count)
    #verify
    if len(track_info) != pos['track_info_size']:
        raise ValueError("Track Info has an unexpected size.")

    ## CHANNEL TABLE
    channel_table = bytearray()
    channel_table.extend(struct.pack('<I', channel_count))
    for i in range(channel_count):
        channel_pos = pos['channel_info'] + i*8 # These 8 byte records are ADPCM information when and if ADPCM is added Info and Seek will need updates
        channel_table.extend(reference(0x4102, channel_pos - pos['channel_table']))
    channel_table = bytes(channel_table)

    ## CHANNEL INFO
    channel_info = build_channel_info(channel_count)
    #verify
    if len(channel_info) != pos['channel_info_size']:
        raise ValueError("Channel Info has an unexpected size.")

    ## INFO HEADER
    info_header = (b'INFO'+ struct.pack('<I', pos['info_size']) 
        + reference(0x4100, pos['stream'] - 0x08)
        + reference(0x0101, pos['track_table'] - 0x08)
        + reference(0x0101, pos['channel_table'] - 0x08)
    )

    ##INFO BLOCK
    info = (
        info_header 
        + stream_info
        + track_table
        + channel_table
        + track_info
        + channel_info
       #+ DSP ADPSM Info
       #+ DSP ADPCM Param
       #+ DSP ADPCM Context
       #+ IMA ADPCM Info
       #+ IMA ADPCM Context
    )

    info = pad_to(info, 0x20)
    #verify
    if len(info) != pos['info_size']:
        raise ValueError("Info has and unexpected size.")
    
    return info

## HEADER

def build_header(info_size, seek_size, data_size):
    # Constraints
    HEADER_SIZE = 0x40
    SECTION_ALIGNMENT = 0x20

    #offsets relative to the start of the file
    info_off = HEADER_SIZE
    seek_off = info_off + info_size
    data_off = seek_off + seek_size
    file_size = data_off + data_size

    header = (
        b'CSTM' 
        + (struct.pack('<HHII', 0xFEFF, 0x40, 0x02000000, file_size)) 
        +(struct.pack('<HH', 3, 0))
        +sized_reference(0x4000, info_off, info_size)
        +sized_reference(0x4001, seek_off, seek_size)
        +sized_reference(0x4002, data_off, data_size)
    )
    header = pad_to(header, 0x40)
    #verify
    if len(header) != 0x40:
        raise ValueError("Header has unexpected size (must be 0x40).")

    return header

## CONVERT

def convert(wav_path, out_path):
    rate, frames, channels = read_wav(wav_path)
    layout = compute_layout(frames)
    
    ## SEEK
    seek = build_seek_block()
    
    #DATA
    data, sample_offset = build_data_block(channels, layout)
    
    ## INFO
    info = build_info_block(
        rate, 
        frames, 
        len(channels), 
        layout, 
        sample_offset
    )

    ## HEADER
    header = build_header(len(info), len(seek), len(data))

    ## VERIFICATION
    result = header + info + seek + data
    verify(result)

    ## WRITING
    destination = Path(out_path)
    temporary = destination.with_name(destination.name + '.tmp')

    try:
        with open(temporary, 'wb') as f:
            f.write(result)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, destination)
    
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return result

def verify(result):
    #Test wether the audio was correctly converted and is ready to ship
    #AI was used to write this due to its size and impracticality,
    #It tests if the file has absolute integrity
    def require(condition, message):
        if not condition:
            raise ValueError(message)

     # 1. HEADER INTEGRITY
    require(len(result) >= 0x40, "File is shorter than the header.")
    require(result[:4] == b'CSTM', "Missing CSTM magic.")

    endian, header_size, version, file_size = struct.unpack_from(
        '<HHII', result, 4
    )

    require(endian == 0xFEFF, "Incorrect endianness marker.")
    require(header_size == 0x40, "Incorrect header size.")
    require(version == 0x02000000, "Unexpected CSTM version.")
    require(file_size == len(result), "Declared file size is incorrect.")

    section_count = struct.unpack_from('<H', result, 0x10)[0]
    require(section_count == 3, "Expected INFO, SEEK, and DATA sections.")

    # 2. SECTION INTEGRITY
    expected_sections = [
        (0x4000, b'INFO'),
        (0x4001, b'SEEK'),
        (0x4002, b'DATA'),
    ]

    sections = {}

    for i, (expected_type, magic) in enumerate(expected_sections):
        ref_pos = 0x14 + i * 0x0C
        type_id, _reserved, offset, size = struct.unpack_from(
            '<HHII', result, ref_pos
        )

        require(type_id == expected_type,
                f"Incorrect section type for {magic.decode()}.")
        require(offset % 0x20 == 0,
                f"{magic.decode()} offset is not aligned.")
        require(size >= 8, f"{magic.decode()} section is too small.")
        require(offset + size <= len(result),
                f"{magic.decode()} extends beyond the file.")
        require(result[offset:offset + 4] == magic,
                f"Missing {magic.decode()} magic.")
        require(struct.unpack_from('<I', result, offset + 4)[0] == size,
                f"{magic.decode()} size field is incorrect.")
        require(size % 0x20 == 0,
                f"{magic.decode()} size is not aligned.")

        sections[expected_type] = (offset, size)

    # Check that sections are ordered and do not overlap.
    previous_end = 0x40
    for section_type, _magic in expected_sections:
        offset, size = sections[section_type]
        require(offset >= previous_end, "Sections overlap or are out of order.")
        previous_end = offset + size

    require(previous_end == len(result),
            "Unexpected bytes after the final section.")

    # 3. INFO METADATA AND REFERENCES
    info_offset, info_size = sections[0x4000]
    data_offset, data_size = sections[0x4002]

    # Top-level INFO references use INFO + 0x08 as their offset base.
    stream_type, _, stream_delta = struct.unpack_from(
        '<HHI', result, info_offset + 0x08
    )
    track_type, _, track_delta = struct.unpack_from(
        '<HHI', result, info_offset + 0x10
    )
    channel_type, _, channel_delta = struct.unpack_from(
        '<HHI', result, info_offset + 0x18
    )

    require(stream_type == 0x4100, "Incorrect Stream Info reference type.")
    require(track_type == 0x0101, "Incorrect track table reference type.")
    require(channel_type == 0x0101, "Incorrect channel table reference type.")

    base = info_offset + 0x08
    stream_pos = base + stream_delta
    track_table_pos = base + track_delta
    channel_table_pos = base + channel_delta
    info_end = info_offset + info_size

    require(info_offset + 0x20 <= stream_pos
            and stream_pos + 0x38 <= info_end,
            "Stream Info is outside INFO.")
    require(info_offset <= track_table_pos
            and track_table_pos + 4 <= info_end,
            "Track table is outside INFO.")
    require(info_offset <= channel_table_pos
            and channel_table_pos + 4 <= info_end,
            "Channel table is outside INFO.")

    encoding = result[stream_pos]
    channel_count = result[stream_pos + 2]
    sample_rate = struct.unpack_from('<I', result, stream_pos + 4)[0]
    frame_count = struct.unpack_from('<I', result, stream_pos + 0x0C)[0]

    require(encoding == 1, "Expected PCM16 encoding.")
    require(1 <= channel_count <= 255, "Invalid channel count.")
    require(sample_rate > 0, "Invalid sample rate.")
    require(frame_count > 0, "Invalid frame count.")

    track_count = struct.unpack_from('<I', result, track_table_pos)[0]
    channel_table_count = struct.unpack_from(
        '<I', result, channel_table_pos
    )[0]

    require(track_count == 1, "Expected exactly one track.")
    require(channel_table_count == channel_count,
            "Channel table count does not match Stream Info.")

    require(track_table_pos + 4 + track_count * 8 <= info_end,
            "Track table extends beyond INFO.")
    require(channel_table_pos + 4 + channel_table_count * 8 <= info_end,
            "Channel table extends beyond INFO.")

    # 4. SAMPLE DATA REFERENCE
    sample_ref_type, _, sample_delta = struct.unpack_from(
        '<HHI', result, stream_pos + 0x30
    )

    require(sample_ref_type == 0x1F00,
            "Incorrect Sample Data reference type.")

    # In this format, the sample reference is relative to DATA + 0x08.
    sample_target = data_offset + 0x08 + sample_delta

    require(sample_target == data_offset + 0x20,
            "Sample reference does not point to DATA + 0x20.")

    # 5. AUDIO BLOCK LAYOUT AND DATA SIZE
    block_count = struct.unpack_from('<I', result, stream_pos + 0x10)[0]
    block_size = struct.unpack_from('<I', result, stream_pos + 0x14)[0]
    block_samples = struct.unpack_from('<I', result, stream_pos + 0x18)[0]
    last_size = struct.unpack_from('<I', result, stream_pos + 0x1C)[0]
    last_samples = struct.unpack_from('<I', result, stream_pos + 0x20)[0]
    last_padded_size = struct.unpack_from('<I', result, stream_pos + 0x24)[0]

    require(block_count > 0, "Invalid block count.")
    require(block_samples == 4096, "Unexpected samples per full block.")
    require(block_size == block_samples * 2,
            "Incorrect PCM16 full-block size.")
    require(1 <= last_samples <= block_samples,
            "Invalid final block sample count.")
    require(last_size == last_samples * 2,
            "Final block byte count is incorrect.")
    require(last_padded_size == align(last_size, 0x20),
            "Final block padding size is incorrect.")

    expected_block_count = (
        frame_count + block_samples - 1
    ) // block_samples

    require(block_count == expected_block_count,
            "Block count does not match frame count.")

    expected_frames = (block_count - 1) * block_samples + last_samples
    require(expected_frames == frame_count,
            "Block sample counts do not match frame count.")

    expected_data_size = (
        0x20
        + ((block_count - 1) * block_size + last_padded_size)
        * channel_count
    )

    require(data_size == expected_data_size,
            "DATA size does not match the audio layout.")

    return True
    
########## OGG CONVERSION ##########
def convert_ogg(ogg_path, out_path, ffmpeg='ffmpeg'):
    with tempfile.TemporaryDirectory() as temporary_directory:
        wav_path = Path(temporary_directory) / 'decoded.wav' #should be ogg.wav

        subprocess.run(
            [
                ffmpeg,
                '-nostdin',
                '-y',
                '-i', str(ogg_path),
                '-map', '0:a:0',
                '-c:a', 'pcm_s16le',
                str(wav_path),
            ],
            check=True,
        )

        return convert(wav_path, out_path)
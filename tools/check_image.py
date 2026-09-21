"""读取 ROCK 5B+ 镜像的分区表，并检查各分区文件系统签名（只读，不修改任何东西）"""
import lzma, struct, uuid, sys, os

PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Downloads", "rock-5b-plus_bookworm_kde_r7.output_512.img.xz")

KNOWN = {
    'c12a7328-f81f-11d2-ba4b-00a0c93ec93b': 'ESP (EFI System) -> 通常是 FAT',
    'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7': 'Microsoft Basic Data -> 通常是 FAT/NTFS',
    '0fc63daf-8483-4772-8e79-3d69d8477de4': 'Linux filesystem -> 通常是 ext4',
    'b921b045-1df0-41c3-af44-4c6f280d3fae': 'Linux root (arm64) -> 通常是 ext4',
    'e3c9e316-0b5c-4db8-817d-f92df00215ae': 'Microsoft Reserved (MSR) -> 无文件系统',
}


def g(b):
    return str(uuid.UUID(bytes_le=bytes(b))).lower()


def identify(buf):
    """根据首扇区判断文件系统"""
    if len(buf) < 1082:
        return '数据不足'
    oem = buf[3:11].decode('ascii', 'replace').strip()
    f1 = buf[0x36:0x3B].decode('ascii', 'replace')
    f2 = buf[0x52:0x57].decode('ascii', 'replace')
    is_ext4 = (buf[1080] == 0x53 and buf[1081] == 0xEF)
    sig = '%02X%02X' % (buf[510], buf[511])
    if f1 == 'FAT32' or f2 == 'FAT32':
        return f'FAT32  (OEM="{oem}")'
    if f1.startswith('FAT'):
        return f'{f1}  (OEM="{oem}")'
    if is_ext4:
        return 'ext4   (superblock 魔数 53EF)'
    if buf[:16] == b'\x00' * 16:
        return '全零 —— 该分区没有写入任何数据！'
    return f'未知 (OEM="{oem}" 引导签名={sig})'


with lzma.open(PATH, 'rb') as f:
    head = f.read(34 * 512)

if head[512:520] != b'EFI PART':
    print('不是 GPT 分区表！前 512 字节引导签名 =',
          '%02X%02X' % (head[510], head[511]))
    sys.exit(1)

n_ent = struct.unpack_from('<I', head, 512 + 80)[0]
e_sz = struct.unpack_from('<I', head, 512 + 84)[0]
disk_guid = g(head[512 + 56:512 + 72])

entries = head[1024:1024 + n_ent * e_sz]
parts = []
for i in range(n_ent):
    e = entries[i * e_sz:(i + 1) * e_sz]
    if len(e) < e_sz or e[:16] == b'\x00' * 16:
        continue
    t = g(e[0:16])
    first = struct.unpack_from('<Q', e, 32)[0]
    last = struct.unpack_from('<Q', e, 40)[0]
    name = e[56:128].decode('utf-16-le', 'replace').rstrip('\x00')
    parts.append((i + 1, t, first, last, name))

print('镜像分区表 (GPT)  磁盘GUID =', disk_guid)
print(f'共 {len(parts)} 个分区\n')
hdr = f"{'#':>2}  {'起始扇区':>10}  {'大小':>11}  {'名称':<14} 类型"
print(hdr)
print('-' * len(hdr))
for n, t, first, last, name in parts:
    mb = (last - first + 1) * 512 / 1024 / 1024
    print(f"{n:>2}  {first:>10}  {mb:>9.1f}MB  {name:<14} {KNOWN.get(t, t)}")

# ---- 读取前两个分区的首扇区，判断文件系统 ----
offsets = sorted({p[2] for p in parts})[:3]
need = offsets[-1] * 512 + 8192
print(f'\n正在解压镜像前 {need/1024/1024:.1f} MB 以检查文件系统签名 …')
with lzma.open(PATH, 'rb') as f:
    buf = f.read(need)

print()
for n, t, first, last, name in parts:
    off = first * 512
    if off + 8192 > len(buf):
        print(f'分区 {n} ({name}): 偏移 {off/1024/1024:.1f}MB 超出本次读取范围，跳过')
        continue
    chunk = buf[off:off + 8192]
    print(f'分区 {n}  ({name or "无名称"})  @ {off/1024/1024:>7.2f} MB  ->  {identify(chunk)}')

#!/usr/bin/env bash
# ContextKeeper 插件构建脚本
# 运行后生成 context-keeper-0.1.0.vsix，可直接安装到 Cursor / VS Code
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXT_DIR="$SCRIPT_DIR/extension"

echo "[1/4] 安装 Node.js 依赖..."
cd "$EXT_DIR"
npm install

echo "[2/4] 编译 TypeScript..."
npm run compile

echo "[3/4] 打包 .vsix..."
# 将 backend 目录软链接（或拷贝）到 extension 内，以便打包进去
if [ ! -d "$EXT_DIR/backend" ]; then
  cp -r "$SCRIPT_DIR/backend" "$EXT_DIR/backend"
  echo "  已拷贝 backend 到 extension 目录"
fi

# 创建占位图标（如无正式图标）
mkdir -p "$EXT_DIR/assets"
if [ ! -f "$EXT_DIR/assets/icon.png" ]; then
  # 用 Python 生成一个简单的 128x128 PNG（纯色占位）
  python3 -c "
import struct, zlib
def make_png(w, h, r, g, b):
    def chunk(name, data):
        c = zlib.crc32(name + data) & 0xffffffff
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', c)
    raw = b''.join(b'\\x00' + bytes([r,g,b]*w) for _ in range(h))
    compressed = zlib.compress(raw)
    return b'\\x89PNG\\r\\n\\x1a\\n' + chunk(b'IHDR', struct.pack('>IIBBBBB',w,h,8,2,0,0,0)) + chunk(b'IDAT',compressed) + chunk(b'IEND',b'')
open('$EXT_DIR/assets/icon.png','wb').write(make_png(128,128,14,99,198))
" 2>/dev/null || echo "  (图标生成跳过，请手动添加 assets/icon.png)"
fi

npx vsce package --no-dependencies --out "$SCRIPT_DIR"

echo ""
echo "[4/4] 完成！"
echo ""
echo "生成文件: $SCRIPT_DIR/context-keeper-0.1.0.vsix"
echo ""
echo "安装方式："
echo "  Cursor: 打开命令面板 → Extensions: Install from VSIX → 选择上述文件"
echo "  VS Code: code --install-extension context-keeper-0.1.0.vsix"

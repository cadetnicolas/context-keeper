#!/usr/bin/env bash
# ContextKeeper 插件构建脚本
# 运行后生成 context-keeper-0.1.0.vsix，包含内置 ONNX 模型，安装后无需联网下载模型
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXT_DIR="$SCRIPT_DIR/extension"
BACKEND_SRC="$SCRIPT_DIR/backend"
MODEL_DIR="$BACKEND_SRC/models/all-MiniLM-L6-v2"

echo "[1/5] 安装 Node.js 依赖..."
cd "$EXT_DIR"
npm install --cache /tmp/npm-cache-ck

echo "[2/5] 编译 TypeScript..."
npm run compile --cache /tmp/npm-cache-ck

echo "[3/5] 下载 / 检查 ONNX 模型..."
mkdir -p "$MODEL_DIR"
if [ ! -f "$MODEL_DIR/model.onnx" ]; then
  echo "  下载 all-MiniLM-L6-v2 ONNX 模型（首次构建需下载 ~87MB）..."
  BASE="https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main"
  curl -L --progress-bar "$BASE/onnx/model.onnx" -o "$MODEL_DIR/model.onnx"
  curl -sL "$BASE/tokenizer.json"        -o "$MODEL_DIR/tokenizer.json"
  curl -sL "$BASE/tokenizer_config.json" -o "$MODEL_DIR/tokenizer_config.json"
  curl -sL "$BASE/special_tokens_map.json" -o "$MODEL_DIR/special_tokens_map.json"
  curl -sL "$BASE/vocab.txt"             -o "$MODEL_DIR/vocab.txt"
else
  echo "  ONNX 模型已存在，跳过下载"
fi

echo "[4/5] 拷贝 backend（含模型）到 extension 目录..."
rm -rf "$EXT_DIR/backend"
cp -r "$BACKEND_SRC" "$EXT_DIR/backend"
rm -rf "$EXT_DIR/backend/venv" "$EXT_DIR/backend/venv_test" \
       "$EXT_DIR/backend/pip_install.log" "$EXT_DIR/backend/server.log"

mkdir -p "$EXT_DIR/assets"
if [ ! -f "$EXT_DIR/assets/icon.png" ]; then
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
" 2>/dev/null || true
fi

echo "[5/5] 打包 .vsix..."
echo y | npx --cache /tmp/npm-cache-ck vsce package --no-dependencies --out "$SCRIPT_DIR"

echo ""
echo "完成！"
echo "生成文件: $SCRIPT_DIR/context-keeper-0.1.0.vsix"
echo "安装: Cursor 命令面板 → Extensions: Install from VSIX → 选择上述文件"
echo "安装后无需手动操作，ContextKeeper 将在后台自动初始化"

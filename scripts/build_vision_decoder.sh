#!/bin/sh
# Build unmodified FFmpeg 9.0.2 with the portable pinned LLVM-MinGW toolchain.
# Called by build_vision_decoder.py; all output and temporary files stay in Yikes.
set -eu
build_root=$1
toolchain="$build_root/toolchain/llvm-mingw-20261104-ucrt-x86_64"
export PATH="$toolchain/bin;$toolchain/busybox/bin"
export TMPDIR="$build_root/tmp"
export TMP="$build_root/tmp"
export TEMP="$build_root/tmp"
export SOURCE_DATE_EPOCH=1789709400
mkdir -p "$TMPDIR"
cd "$build_root/source/ffmpeg-9.0.2"
./configure \
  --target-os=mingw32 --arch=x86_64 --enable-cross-compile \
  --cc=x86_64-w64-mingw32-clang --cxx=x86_64-w64-mingw32-clang++ \
  --ar=x86_64-w64-mingw32-ar --ranlib=x86_64-w64-mingw32-ranlib \
  --nm=x86_64-w64-mingw32-nm --strip=x86_64-w64-mingw32-strip \
  --disable-autodetect --disable-network --disable-everything \
  --disable-doc --disable-debug --disable-x86asm --disable-shared --enable-static \
  --disable-programs --enable-ffmpeg --disable-swresample \
  --enable-avdevice --enable-indev=dshow \
  --enable-protocol=fd,pipe --enable-demuxer=avi,matroska,mov \
  --enable-decoder=rawvideo,mjpeg,h264,hevc,mpeg4,msmpeg4v3,vp8,vp9,png \
  --enable-parser=h264,hevc,mpeg4video,vp8,vp9,mjpeg \
  --enable-encoder=rawvideo --enable-muxer=rawvideo \
  --enable-filter=buffer,buffersink,format,scale,fps,null,select,showinfo,pad \
  --extra-ldflags='-static -Wl,--no-insert-timestamp'
mingw32-make -j4 SHELL=sh ffmpeg.exe

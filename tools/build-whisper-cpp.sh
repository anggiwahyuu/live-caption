#!/usr/bin/env bash
# Builds whisper.cpp's whisper-server for Linux x86-64, for computers without an NVIDIA GPU (lib/whispercpp.py).
# Run by .github/workflows/whisper-cpp.yml on Ubuntu 22.04 (an old glibc, so the result runs on older
# distributions too); works the same in an ubuntu:22.04 container. Needs: build-essential cmake ninja-build
# git curl xz-utils libvulkan-dev (to link against; the computer's own loader is used). Output: dist/whisper-cpp-linux-x64.tar.gz
#
# Portable on purpose: the official and some third-party Linux builds are compiled for the build machine's CPU
# (e.g. with AVX-512) and crash with "illegal instruction" on most AMD Ryzen and Intel desktop CPUs.
#   GGML_NATIVE=OFF + GGML_BACKEND_DL + GGML_CPU_ALL_VARIANTS: one CPU library per instruction set, the best
#     one for the computer is picked when it starts
#   GGML_VULKAN: AMD / Intel / NVIDIA GPUs; without a Vulkan driver the CPU is used
#   GGML_OPENMP=OFF: no libgomp needed on the user's computer
#   RPATH $ORIGIN: the libraries are found next to whisper-server, no LD_LIBRARY_PATH
set -euo pipefail

WHISPER_CPP_VERSION=${WHISPER_CPP_VERSION:-v1.9.5}
VULKAN_SDK_VERSION=${VULKAN_SDK_VERSION:-1.4.363.0}  # for glslc and the headers: Ubuntu 22.04 has no glslc
WORK=${WORK:-$PWD/build-whisper-cpp}
DIST=${DIST:-$PWD/dist}
PKG=whisper-cpp-linux-x64

mkdir -p "$WORK" "$DIST"
cd "$WORK"
if [[ ! -d sdk ]]; then
  curl -fsSL -o sdk.tar.xz \
    "https://sdk.lunarg.com/sdk/download/$VULKAN_SDK_VERSION/linux/vulkansdk-linux-x86_64-$VULKAN_SDK_VERSION.tar.xz"
  mkdir sdk
  tar -xJf sdk.tar.xz -C sdk --strip-components=1
  rm sdk.tar.xz
fi
export VULKAN_SDK=$WORK/sdk/x86_64
export PATH=$VULKAN_SDK/bin:$PATH
[[ -d src ]] || git clone --quiet --depth 1 --branch "$WHISPER_CPP_VERSION" https://github.com/ggml-org/whisper.cpp src

# shellcheck disable=SC2016  # '$ORIGIN' is for the linker, not the shell
cmake -S src -B out -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON -DGGML_VULKAN=ON -DGGML_OPENMP=OFF \
  -DWHISPER_BUILD_SERVER=ON -DWHISPER_BUILD_TESTS=OFF -DWHISPER_SDL2=OFF \
  -DCMAKE_PREFIX_PATH="$VULKAN_SDK" -DCMAKE_CXX_FLAGS="-I$VULKAN_SDK/include" \
  -DCMAKE_INSTALL_RPATH='$ORIGIN' -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON
cmake --build out --target whisper-server -j "$(nproc)"

rm -rf "$PKG"
mkdir "$PKG"
cp -a out/bin/whisper-server out/bin/*.so* "$PKG/"
strip --strip-unneeded "$PKG/whisper-server" "$PKG"/*.so
cp src/LICENSE "$PKG/LICENSE-whisper.cpp"  # MIT: the license goes with the copies
cp src/samples/jfk.wav "$PKG/"             # a short test sentence (public domain), for checks
echo "whisper.cpp $WHISPER_CPP_VERSION (Vulkan SDK $VULKAN_SDK_VERSION), built $(date -u +%Y-%m-%d)" > "$PKG/VERSION"
tar -czf "$DIST/$PKG.tar.gz" -C "$PKG" .
echo "Built $DIST/$PKG.tar.gz ($(du -h "$DIST/$PKG.tar.gz" | cut -f1))"

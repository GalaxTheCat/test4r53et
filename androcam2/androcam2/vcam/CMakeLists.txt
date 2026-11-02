# Builds androcam_vcam_<arch>.dll with MSVC. Usage (see .github/workflows/build.yml):
#   cmake -S vcam -B build-x64 -A x64   -DBASECLASSES_DIR=<...>/directshow/baseclasses -DARCH_NAME=x64
#   cmake -S vcam -B build-x86 -A Win32 -DBASECLASSES_DIR=<...>/directshow/baseclasses -DARCH_NAME=x86
cmake_minimum_required(VERSION 3.15)
project(androcam_vcam CXX)
if(NOT BASECLASSES_DIR)
  message(FATAL_ERROR "Set -DBASECLASSES_DIR to the DirectShow baseclasses folder (Windows-classic-samples)")
endif()
if(NOT ARCH_NAME)
  set(ARCH_NAME x64)
endif()
file(GLOB BASE_SRC "${BASECLASSES_DIR}/*.cpp")
add_library(androcam_vcam SHARED vcam.cpp vcam.def ${BASE_SRC})
target_include_directories(androcam_vcam PRIVATE "${BASECLASSES_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_definitions(androcam_vcam PRIVATE UNICODE _UNICODE WIN32 _WINDOWS _CRT_SECURE_NO_WARNINGS
                           _CRT_NONSTDC_NO_DEPRECATE NO_SHLWAPI_STRFCNS)
target_compile_options(androcam_vcam PRIVATE /permissive /EHsc /W3 /wd4996 /wd4244 /wd4267 /wd4100 /wd4838 /wd4302 /wd4311 /wd4312 /wd4091 /wd4311 /wd4458)
target_link_libraries(androcam_vcam PRIVATE strmiids winmm ole32 oleaut32 uuid advapi32 user32 gdi32 shlwapi)
set_target_properties(androcam_vcam PROPERTIES OUTPUT_NAME "androcam_vcam_${ARCH_NAME}" PREFIX "")
# statically link the C runtime so the DLL has no redistributable dependency inside other people's processes
set_property(TARGET androcam_vcam PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")

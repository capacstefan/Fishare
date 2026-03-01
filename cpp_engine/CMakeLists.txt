cmake_minimum_required(VERSION 3.18)
project(cpp_engine CXX)

# ── Compiler requirements ─────────────────────────────
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Default to Release for performance; override with -DCMAKE_BUILD_TYPE=Debug
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# ── Compiler flags ────────────────────────────────────
if(MSVC)
    add_compile_options(/W3 /O2 /MP)
    # Suppress C4996 for cross-platform fopen / fread / fwrite
    add_compile_definitions(_CRT_SECURE_NO_WARNINGS)
else()
    add_compile_options(-Wall -Wextra -O3)
endif()

# ── OpenSSL ───────────────────────────────────────────
find_package(OpenSSL REQUIRED)

# ── pybind11 via FetchContent ─────────────────────────
# Downloads pybind11 at configure time if not already present.
# Requires internet access during the first cmake configure run.
include(FetchContent)

FetchContent_Declare(
    pybind11
    GIT_REPOSITORY https://github.com/pybind/pybind11.git
    GIT_TAG        v2.12.0   # pin to a stable release
    GIT_SHALLOW    TRUE
)
FetchContent_MakeAvailable(pybind11)

# ── Source files ──────────────────────────────────────
set(SOURCES
    src/engine.cpp
    src/bindings.cpp
)

# ── Build the extension module ────────────────────────
# pybind11_add_module sets the correct suffix (.pyd / .so / .dylib),
# defines Py_LIMITED_API when appropriate, and links Python.
pybind11_add_module(cpp_engine ${SOURCES})

target_include_directories(cpp_engine PRIVATE
    src/
    ${OPENSSL_INCLUDE_DIR}
)

target_link_libraries(cpp_engine PRIVATE
    OpenSSL::SSL
    OpenSSL::Crypto
)

if(WIN32)
    # Link Winsock (needed for send/recv on Windows)
    target_link_libraries(cpp_engine PRIVATE ws2_32)
endif()

# ── Install into project root ─────────────────────────
# The build_cpp.py helper copies the built module to the project root so that
# `import cpp_engine` works without modifying sys.path.
#
# Alternatively, set LIBRARY_OUTPUT_DIRECTORY here:
set_target_properties(cpp_engine PROPERTIES
    # Windows .pyd files are listed as RUNTIME artifacts by CMake
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_SOURCE_DIR}/.."
    RUNTIME_OUTPUT_DIRECTORY_DEBUG   "${CMAKE_SOURCE_DIR}/.."
    RUNTIME_OUTPUT_DIRECTORY         "${CMAKE_SOURCE_DIR}/.."
    LIBRARY_OUTPUT_DIRECTORY_RELEASE "${CMAKE_SOURCE_DIR}/.."
    LIBRARY_OUTPUT_DIRECTORY_DEBUG   "${CMAKE_SOURCE_DIR}/.."
    LIBRARY_OUTPUT_DIRECTORY         "${CMAKE_SOURCE_DIR}/.."
)

# ── Summary ───────────────────────────────────────────
message(STATUS "")
message(STATUS "=== cpp_engine build configuration ===")
message(STATUS "  Build type   : ${CMAKE_BUILD_TYPE}")
message(STATUS "  OpenSSL      : ${OPENSSL_VERSION}")
message(STATUS "  OpenSSL dirs : incl=${OPENSSL_INCLUDE_DIR}")
message(STATUS "  Python       : ${Python_VERSION} (via pybind11)")
message(STATUS "  C++ standard : ${CMAKE_CXX_STANDARD}")
message(STATUS "=======================================")
message(STATUS "")

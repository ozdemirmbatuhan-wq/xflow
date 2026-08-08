cmake_minimum_required(VERSION 3.20)
project(aeropt-flow5-runner LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(FLOW5_SOURCE_DIR "" CACHE PATH "flow5 v7.57 source directory")
set(FLOW5_BUILD_DIR "" CACHE PATH "flow5 v7.57 build directory")
set(GMSH_ROOT "" CACHE PATH "Gmsh SDK root")
set(OCC_INCLUDE_DIR "" CACHE PATH "OpenCascade include directory")
set(OPENBLAS_ROOT "" CACHE PATH "OpenBLAS installation root")
set(FLOW5_VERSION "7.57" CACHE STRING "flow5 version reported by the runner")
option(FLOW5_USES_MKL "Link the same Intel MKL runtime used to build flow5" ON)

if(NOT EXISTS "${FLOW5_SOURCE_DIR}/flow5-lib/api/api.h")
    message(FATAL_ERROR "Set FLOW5_SOURCE_DIR to the flow5 v7.57 source tree")
endif()

find_package(Qt6 REQUIRED COMPONENTS Core)

set(FLOW5_LIBRARY_HINTS
    "${FLOW5_BUILD_DIR}/flow5-lib"
    "${FLOW5_BUILD_DIR}/release/flow5-lib"
    "${FLOW5_BUILD_DIR}/Release/flow5-lib")
set(FLOW5_IO_LIBRARY_HINTS
    "${FLOW5_BUILD_DIR}/flow5-io-lib"
    "${FLOW5_BUILD_DIR}/release/flow5-io-lib"
    "${FLOW5_BUILD_DIR}/Release/flow5-io-lib")
set(XFOIL_LIBRARY_HINTS
    "${FLOW5_BUILD_DIR}/XFoil-lib"
    "${FLOW5_BUILD_DIR}/release/XFoil-lib"
    "${FLOW5_BUILD_DIR}/Release/XFoil-lib")

find_library(FLOW5_LIBRARY NAMES flow5-lib PATHS ${FLOW5_LIBRARY_HINTS} REQUIRED NO_DEFAULT_PATH)
find_library(FLOW5_IO_LIBRARY NAMES flow5-io-lib PATHS ${FLOW5_IO_LIBRARY_HINTS} REQUIRED NO_DEFAULT_PATH)
find_library(XFOIL_LIBRARY NAMES XFoil1 XFoil PATHS ${XFOIL_LIBRARY_HINTS} REQUIRED)
find_library(GMSH_LIBRARY NAMES gmsh.dll.lib gmsh PATHS "${GMSH_ROOT}/lib" REQUIRED)

add_executable(aeropt-flow5-runner main.cpp)
target_compile_definitions(aeropt-flow5-runner PRIVATE AEROPT_FLOW5_VERSION="${FLOW5_VERSION}")
target_include_directories(aeropt-flow5-runner PRIVATE
    "${FLOW5_SOURCE_DIR}/flow5-lib"
    "${FLOW5_SOURCE_DIR}/flow5-lib/api"
    "${FLOW5_SOURCE_DIR}/flow5-io-lib/api"
    "${FLOW5_SOURCE_DIR}/XFoil-lib"
    "${GMSH_ROOT}/include"
    "${OCC_INCLUDE_DIR}")
target_link_libraries(aeropt-flow5-runner PRIVATE
    ${FLOW5_LIBRARY}
    ${FLOW5_IO_LIBRARY}
    ${XFOIL_LIBRARY}
    ${GMSH_LIBRARY}
    Qt6::Core)

if(MSVC)
    target_compile_options(aeropt-flow5-runner PRIVATE /Zc:__cplusplus /EHsc /utf-8 /permissive-)
    if(FLOW5_USES_MKL)
        find_package(MKL CONFIG REQUIRED)
        find_package(TBB CONFIG REQUIRED)
        target_link_libraries(aeropt-flow5-runner PRIVATE MKL::MKL TBB::tbb)
        target_compile_definitions(aeropt-flow5-runner PRIVATE INTEL_MKL)
    else()
        find_path(OPENBLAS_INCLUDE_DIR cblas.h PATHS "${OPENBLAS_ROOT}/include" REQUIRED NO_DEFAULT_PATH)
        find_library(OPENBLAS_LIBRARY NAMES openblas PATHS "${OPENBLAS_ROOT}/lib" REQUIRED NO_DEFAULT_PATH)
        target_include_directories(aeropt-flow5-runner PRIVATE "${OPENBLAS_INCLUDE_DIR}")
        target_link_libraries(aeropt-flow5-runner PRIVATE "${OPENBLAS_LIBRARY}")
        target_compile_definitions(aeropt-flow5-runner PRIVATE OPENBLAS)
    endif()
elseif(UNIX AND NOT APPLE)
    find_package(OpenBLAS REQUIRED)
    target_link_libraries(aeropt-flow5-runner PRIVATE OpenBLAS::OpenBLAS)
    target_compile_definitions(aeropt-flow5-runner PRIVATE OPENBLAS)
endif()

install(TARGETS aeropt-flow5-runner RUNTIME DESTINATION bin)

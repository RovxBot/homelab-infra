# Use the checksum-verified SDL sources already present in the recovery image.
# Keep the project's expected build/_deps path so its source graph audit remains
# effective. Never exempt /opt or alter the audit's permitted source roots.
get_property(_recovery_try_compile GLOBAL PROPERTY IN_TRY_COMPILE)
if(NOT _recovery_try_compile)
  set(_recovery_sdl "${CMAKE_BINARY_DIR}/_deps/sdl3-src")
  if(NOT EXISTS "${_recovery_sdl}/CMakeLists.txt")
    file(MAKE_DIRECTORY "${_recovery_sdl}")
    file(COPY "/opt/dependencies/sdl3/" DESTINATION "${_recovery_sdl}")
  endif()
  set(FETCHCONTENT_SOURCE_DIR_SDL3 "${_recovery_sdl}" CACHE PATH "Pinned local SDL sources" FORCE)
  set(FETCHCONTENT_FULLY_DISCONNECTED ON CACHE BOOL "No upstream dependency fetch" FORCE)
endif()

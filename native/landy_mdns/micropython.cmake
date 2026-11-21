add_library(usermod_landy_mdns INTERFACE)

target_sources(usermod_landy_mdns INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modlandy_mdns.c
)

target_include_directories(usermod_landy_mdns INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
)

# The ESP32 port's main IDF component already declares and links its pinned
# Espressif mDNS dependency.  Do not add that IDF target to this INTERFACE
# library: MicroPython recursively treats linked INTERFACE targets as user
# modules while gathering sources and include paths.
target_link_libraries(usermod INTERFACE usermod_landy_mdns)

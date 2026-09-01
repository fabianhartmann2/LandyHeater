#define MICROPY_HW_BOARD_NAME               "DFRobot DFR0975-U N16R8"
#define MICROPY_HW_MCU_NAME                 "ESP32S3"

// Keep the generic S3 UART REPL available for recovery and diagnostics.
#define MICROPY_HW_ENABLE_UART_REPL         (1)

// These are the upstream ESP32_GENERIC_S3 defaults. Product code supplies
// explicit board-profile pins and does not rely on these implicit defaults.
#define MICROPY_HW_I2C0_SCL                 (9)
#define MICROPY_HW_I2C0_SDA                 (8)


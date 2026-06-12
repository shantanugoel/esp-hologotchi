// SPDX-License-Identifier: MIT

#include "wokwi-api.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define WIDTH 128u
#define HEIGHT 128u
#define RGBA_BYTES (WIDTH * HEIGHT * 4u)
#define SPI_CHUNK 4096u

#define CMD_DISPLAY_OFF 0xAE
#define CMD_DISPLAY_ON 0xAF
#define CMD_SET_REMAP 0xA0
#define CMD_SET_COLUMN 0x15
#define CMD_SET_ROW 0x75
#define CMD_WRITE_RAM 0x5C

#define REMAP_VERTICAL_INCREMENT 0x01
#define REMAP_MIRROR_H 0x02
#define REMAP_BGR 0x04
#define REMAP_FLIP_V 0x10

typedef struct {
  pin_t cs_pin;
  pin_t dc_pin;
  pin_t rst_pin;
  spi_dev_t spi;
  buffer_t framebuffer;
  bool display_on;
  bool vertical_increment;
  bool mirror_h;
  bool flip_v;
  bool bgr;
  uint8_t current_cmd;
  uint8_t args[3];
  uint8_t args_expected;
  uint8_t args_received;
  uint32_t col_start;
  uint32_t col_end;
  uint32_t row_start;
  uint32_t row_end;
  uint32_t next_col;
  uint32_t next_row;
  uint32_t pending_write_bytes;
  uint8_t pixel_bytes[2];
  uint8_t pixel_byte_count;
  uint8_t spi_byte;
  uint8_t spi_chunk[SPI_CHUNK];
  uint8_t *rgba;
} chip_state_t;

static void chip_pin_change(void *user_data, pin_t pin, uint32_t value);
static void chip_spi_done(void *user_data, uint8_t *buffer, uint32_t count);

static uint32_t clamp_u8(uint8_t value, uint32_t max_value) {
  return value > max_value ? max_value : value;
}

static void blank_display(chip_state_t *chip) {
  static const uint8_t zeros[256] = {0};

  for (uint32_t offset = 0; offset < RGBA_BYTES; offset += sizeof(zeros)) {
    uint32_t len = RGBA_BYTES - offset;
    if (len > sizeof(zeros)) {
      len = sizeof(zeros);
    }
    buffer_write(chip->framebuffer, offset, (void *)zeros, len);
  }
}

static void present_display(chip_state_t *chip) {
  if (!chip->display_on) {
    return;
  }
  buffer_write(chip->framebuffer, 0, chip->rgba, RGBA_BYTES);
}

static void reset_panel(chip_state_t *chip) {
  memset(chip->rgba, 0, RGBA_BYTES);
  chip->display_on = false;
  chip->vertical_increment = false;
  chip->mirror_h = false;
  chip->flip_v = false;
  chip->bgr = false;
  chip->current_cmd = 0;
  chip->args_expected = 0;
  chip->args_received = 0;
  chip->col_start = 0;
  chip->col_end = WIDTH - 1;
  chip->row_start = 0;
  chip->row_end = HEIGHT - 1;
  chip->next_col = chip->col_start;
  chip->next_row = chip->row_start;
  chip->pending_write_bytes = 0;
  chip->pixel_byte_count = 0;
  blank_display(chip);
}

static uint8_t arg_count_for_cmd(uint8_t cmd) {
  switch (cmd) {
    case CMD_SET_COLUMN:
    case CMD_SET_ROW:
      return 2;
    case CMD_SET_REMAP:
      return 1;
    case 0xFD:
    case 0xB3:
    case 0xCA:
    case 0xA1:
    case 0xA2:
    case 0xB5:
    case 0xAB:
    case 0xC7:
    case 0xB1:
    case 0xB6:
    case 0xBE:
      return 1;
    case 0xB4:
    case 0xC1:
      return 3;
    default:
      return 0;
  }
}

static void normalize_window(uint32_t *start, uint32_t *end, uint32_t max_value) {
  if (*start > max_value) {
    *start = max_value;
  }
  if (*end > max_value) {
    *end = max_value;
  }
  if (*end < *start) {
    uint32_t tmp = *start;
    *start = *end;
    *end = tmp;
  }
}

static void begin_write_ram(chip_state_t *chip) {
  chip->next_col = chip->col_start;
  chip->next_row = chip->row_start;
  chip->pending_write_bytes =
      (chip->col_end - chip->col_start + 1u) * (chip->row_end - chip->row_start + 1u) * 2u;
  chip->pixel_byte_count = 0;
}

static void apply_current_command(chip_state_t *chip) {
  switch (chip->current_cmd) {
    case CMD_DISPLAY_OFF:
      chip->display_on = false;
      blank_display(chip);
      break;
    case CMD_DISPLAY_ON:
      chip->display_on = true;
      present_display(chip);
      break;
    case CMD_SET_REMAP:
      chip->vertical_increment = (chip->args[0] & REMAP_VERTICAL_INCREMENT) != 0;
      chip->mirror_h = (chip->args[0] & REMAP_MIRROR_H) != 0;
      chip->flip_v = (chip->args[0] & REMAP_FLIP_V) != 0;
      chip->bgr = (chip->args[0] & REMAP_BGR) != 0;
      break;
    case CMD_SET_COLUMN:
      chip->col_start = clamp_u8(chip->args[0], WIDTH - 1);
      chip->col_end = clamp_u8(chip->args[1], WIDTH - 1);
      normalize_window(&chip->col_start, &chip->col_end, WIDTH - 1);
      break;
    case CMD_SET_ROW:
      chip->row_start = clamp_u8(chip->args[0], HEIGHT - 1);
      chip->row_end = clamp_u8(chip->args[1], HEIGHT - 1);
      normalize_window(&chip->row_start, &chip->row_end, HEIGHT - 1);
      break;
    case CMD_WRITE_RAM:
      begin_write_ram(chip);
      break;
    default:
      break;
  }

  chip->args_expected = 0;
  chip->args_received = 0;
}

static void handle_command_byte(chip_state_t *chip, uint8_t value) {
  chip->current_cmd = value;
  chip->args_expected = arg_count_for_cmd(value);
  chip->args_received = 0;

  if (chip->args_expected == 0) {
    apply_current_command(chip);
  }
}

static void handle_data_byte(chip_state_t *chip, uint8_t value) {
  if (chip->pending_write_bytes > 0) {
    return;
  }
  if (chip->args_expected == 0) {
    return;
  }
  if (chip->args_received >= sizeof(chip->args)) {
    return;
  }

  chip->args[chip->args_received++] = value;
  if (chip->args_received == chip->args_expected) {
    apply_current_command(chip);
  }
}

static void advance_cursor(chip_state_t *chip) {
  if (chip->vertical_increment) {
    if (chip->next_row < chip->row_end) {
      chip->next_row++;
      return;
    }

    chip->next_row = chip->row_start;
    if (chip->next_col < chip->col_end) {
      chip->next_col++;
    }
    return;
  }

  if (chip->next_col < chip->col_end) {
    chip->next_col++;
    return;
  }

  chip->next_col = chip->col_start;
  if (chip->next_row < chip->row_end) {
    chip->next_row++;
  }
}

static void write_pixel(chip_state_t *chip, uint16_t word) {
  uint8_t red = (uint8_t)((word >> 11) & 0x1Fu);
  uint8_t green = (uint8_t)((word >> 5) & 0x3Fu);
  uint8_t blue = (uint8_t)(word & 0x1Fu);
  uint32_t x = chip->next_col;
  uint32_t y = chip->next_row;
  uint32_t offset;

  if (chip->mirror_h) {
    x = (WIDTH - 1u) - x;
  }
  if (chip->flip_v) {
    y = (HEIGHT - 1u) - y;
  }

  offset = (y * WIDTH + x) * 4u;
  chip->rgba[offset] = (red << 3) | (red >> 2);
  chip->rgba[offset + 1] = (green << 2) | (green >> 4);
  chip->rgba[offset + 2] = (blue << 3) | (blue >> 2);
  chip->rgba[offset + 3] = 0xFF;

  advance_cursor(chip);
}

static void handle_ram_bytes(chip_state_t *chip, uint8_t *buffer, uint32_t count) {
  for (uint32_t i = 0; i < count; i++) {
    chip->pixel_bytes[chip->pixel_byte_count++] = buffer[i];
    chip->pending_write_bytes--;

    if (chip->pixel_byte_count == 2) {
      uint16_t word = ((uint16_t)chip->pixel_bytes[0] << 8) | chip->pixel_bytes[1];
      chip->pixel_byte_count = 0;

      if (chip->bgr) {
        uint16_t red = (word >> 11) & 0x1Fu;
        uint16_t green = (word >> 5) & 0x3Fu;
        uint16_t blue = word & 0x1Fu;
        word = (uint16_t)((blue << 11) | (green << 5) | red);
      }

      write_pixel(chip, word);
    }
  }

  if (chip->pending_write_bytes == 0 && chip->pixel_byte_count == 0) {
    present_display(chip);
  }
}

static void start_next_spi_read(chip_state_t *chip) {
  uint32_t count = 1;
  uint8_t *buffer = &chip->spi_byte;

  if (pin_read(chip->cs_pin) != LOW) {
    return;
  }

  if (chip->pending_write_bytes > 0 && pin_read(chip->dc_pin) == HIGH) {
    count = chip->pending_write_bytes;
    if (count > SPI_CHUNK) {
      count = SPI_CHUNK;
    }
    buffer = chip->spi_chunk;
  }

  spi_start(chip->spi, buffer, count);
}

void chip_init(void) {
  chip_state_t *chip = malloc(sizeof(chip_state_t));
  uint32_t fb_width = 0;
  uint32_t fb_height = 0;

  if (chip == NULL) {
    return;
  }

  memset(chip, 0, sizeof(*chip));
  chip->rgba = malloc(RGBA_BYTES);
  if (chip->rgba == NULL) {
    free(chip);
    return;
  }

  chip->framebuffer = framebuffer_init(&fb_width, &fb_height);
  chip->cs_pin = pin_init("CS", INPUT_PULLUP);
  chip->dc_pin = pin_init("DC", INPUT);
  chip->rst_pin = pin_init("RST", INPUT_PULLUP);

  const pin_watch_config_t cs_watch = {
    .edge = BOTH,
    .pin_change = chip_pin_change,
    .user_data = chip,
  };
  const pin_watch_config_t rst_watch = {
    .edge = FALLING,
    .pin_change = chip_pin_change,
    .user_data = chip,
  };
  const spi_config_t spi_config = {
    .sck = pin_init("SCK", INPUT),
    .miso = NO_PIN,
    .mosi = pin_init("MOSI", INPUT),
    .mode = 0,
    .done = chip_spi_done,
    .user_data = chip,
  };

  chip->spi = spi_init(&spi_config);
  pin_watch(chip->cs_pin, &cs_watch);
  pin_watch(chip->rst_pin, &rst_watch);

  (void)fb_width;
  (void)fb_height;
  reset_panel(chip);
}

static void chip_pin_change(void *user_data, pin_t pin, uint32_t value) {
  chip_state_t *chip = (chip_state_t *)user_data;

  if (pin == chip->rst_pin) {
    if (value == LOW) {
      reset_panel(chip);
    }
    return;
  }

  if (pin != chip->cs_pin) {
    return;
  }

  if (value == LOW) {
    start_next_spi_read(chip);
  } else {
    spi_stop(chip->spi);
  }
}

static void chip_spi_done(void *user_data, uint8_t *buffer, uint32_t count) {
  chip_state_t *chip = (chip_state_t *)user_data;
  bool is_data = pin_read(chip->dc_pin) == HIGH;

  if (count == 0) {
    return;
  }

  if (is_data) {
    if (chip->pending_write_bytes > 0) {
      handle_ram_bytes(chip, buffer, count);
    } else {
      for (uint32_t i = 0; i < count; i++) {
        handle_data_byte(chip, buffer[i]);
      }
    }
  } else {
    for (uint32_t i = 0; i < count; i++) {
      handle_command_byte(chip, buffer[i]);
    }
  }

  start_next_spi_read(chip);
}

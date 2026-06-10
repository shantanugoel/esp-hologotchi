# SPDX-License-Identifier: MIT

SOURCES = src/ssd1351.c
API_HEADER = src/wokwi-api.h
TARGET = dist/ssd1351.wasm
IMAGE = wokwi/builder-clang-wasm:latest
DOCKER_RUN = docker run --rm -u $(shell id -u):$(shell id -g) -v $(CURDIR):/src -w /src $(IMAGE)

.PHONY: all
all: $(TARGET) dist/ssd1351.json

.PHONY: clean
clean:
	rm -rf dist

dist:
	mkdir -p dist

$(API_HEADER):
	curl -fsSL https://wokwi.com/api/chips/wokwi-api.h -o $@

$(TARGET): dist $(SOURCES) $(API_HEADER)
	$(DOCKER_RUN) clang --target=wasm32-unknown-wasi --sysroot /opt/wasi-libc -std=c11 -O2 -Wall -Wextra -Werror -Wno-unused-function -nostartfiles -Wl,--import-memory -Wl,--export-table -Wl,--no-entry -o $(TARGET) $(SOURCES)

dist/ssd1351.json: dist chip.json
	cp chip.json $@

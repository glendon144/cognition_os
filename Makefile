PREFIX ?= /usr/local
BINDIR ?= $(PREFIX)/bin
DATADIR ?= $(PREFIX)/share/cognition-os
CC ?= cc
CFLAGS ?= -O2
STRICT_CFLAGS = -std=c99 -Wall -Wextra -Wpedantic

.PHONY: all test clean install

all: build/cogboot

build/cogboot: src/cogboot.c src/process.c src/process.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(STRICT_CFLAGS) -o $@ src/cogboot.c src/process.c $(LDFLAGS)

test: build/cogboot
	COGBOOT=./build/cogboot sh tests/test_cogboot.sh

clean:
	rm -f build/cogboot

install: build/cogboot
	install -d "$(DESTDIR)$(BINDIR)" "$(DESTDIR)$(DATADIR)"
	install -m 755 build/cogboot "$(DESTDIR)$(BINDIR)/cogboot-native"
	install -m 644 cogboot.manifest "$(DESTDIR)$(DATADIR)/cogboot.manifest"

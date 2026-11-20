.PHONY: all clean check

all:
	mkdir -p zig-out/bin
	zig build-exe src/main.zig -O ReleaseSafe -femit-bin=zig-out/bin/rvaudit

check: all
	./zig-out/bin/rvaudit /var/lib/rvaudit/cases/smoke.json /tmp/rvaudit-smoke.json

clean:
	rm -rf zig-out zig-cache .zig-cache

OCAMLC=ocamlc

all: nsec3audit

nsec3audit: src/main.ml
	$(OCAMLC) -o nsec3audit unix.cma src/main.ml

clean:
	rm -f nsec3audit src/*.cmi src/*.cmo

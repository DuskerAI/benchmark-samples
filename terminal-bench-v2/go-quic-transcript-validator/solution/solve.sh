#!/bin/bash
set -euo pipefail

cd /app/quicval

cat > internal/qtrace/qtrace.go <<'GO'
package qtrace

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
)

type Report struct {
	Version     string             `json:"version"`
	Summary     Summary            `json:"summary"`
	Connections []ConnectionReport `json:"connections"`
}

type Summary struct {
	TraceCount      int  `json:"trace_count"`
	PacketCount     int  `json:"packet_count"`
	ConnectionCount int  `json:"connection_count"`
	ProblemCount    int  `json:"problem_count"`
	Valid           bool `json:"valid"`
}

type ConnectionReport struct {
	ConnectionID        string          `json:"connection_id"`
	RetryValid          bool            `json:"retry_valid"`
	PacketCount         int             `json:"packet_count"`
	Packets             []PacketReport  `json:"packets"`
	TransportParameters EndpointParams  `json:"transport_parameters"`
	Problems            []ProblemReport `json:"problems"`
}

type EndpointParams struct {
	Client []ParamReport `json:"client"`
	Server []ParamReport `json:"server"`
}

type PacketReport struct {
	Index        int    `json:"index"`
	Type         string `json:"type"`
	Endpoint     string `json:"endpoint"`
	DCID         string `json:"dcid"`
	SCID         string `json:"scid"`
	PacketNumber *int64 `json:"packet_number"`
	Token        string `json:"token,omitempty"`
}

type ParamReport struct {
	ID    string `json:"id"`
	Value string `json:"value"`
}

type ProblemReport struct {
	Code        string `json:"code"`
	PacketIndex int    `json:"packet_index"`
	Detail      string `json:"detail"`
}

type packet struct {
	index     int
	raw       []byte
	typ       string
	dcid      []byte
	scid      []byte
	token     []byte
	pnLen     int
	truncated uint64
	payload   []byte
	tag       []byte
}

type connState struct {
	id        string
	odcid     []byte
	hadRetry  bool
	badRetry  bool
	packets   []PacketReport
	problems  []ProblemReport
	params    map[string][]ParamReport
	seenPN    map[string]bool
	largestPN map[string]*int64
}

func ValidateFile(path string) (Report, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Report{}, err
	}
	packets, err := readBundle(data)
	if err != nil {
		return Report{}, err
	}
	states := map[string]*connState{}
	problemCount := 0

	for _, pkt := range packets {
		endpoint := "client"
		if pkt.typ == "retry" {
			endpoint = "server"
		} else if len(pkt.payload) > 0 && pkt.payload[0] == 2 {
			endpoint = "server"
		}
		key := connectionKey(pkt, endpoint)
		st := states[key]
		if st == nil {
			st = &connState{
				id:        key,
				odcid:     append([]byte(nil), pkt.dcid...),
				packets:   []PacketReport{},
				problems:  []ProblemReport{},
				params:    map[string][]ParamReport{"client": {}, "server": {}},
				seenPN:    map[string]bool{},
				largestPN: map[string]*int64{"client": nil, "server": nil},
			}
			states[key] = st
		}

		if pkt.typ == "retry" {
			st.hadRetry = true
			ok := validRetry(st.odcid, pkt.raw, pkt.tag)
			if !ok {
				st.badRetry = true
				st.problems = append(st.problems, ProblemReport{Code: "bad_retry_integrity", PacketIndex: pkt.index, Detail: "retry"})
				problemCount++
			}
			st.packets = append(st.packets, PacketReport{Index: pkt.index, Type: pkt.typ, Endpoint: endpoint, DCID: hex.EncodeToString(pkt.dcid), SCID: hex.EncodeToString(pkt.scid), PacketNumber: nil, Token: hex.EncodeToString(pkt.token)})
			continue
		}

		fullPN := reconstructPacketNumber(pkt.truncated, pkt.pnLen, st.largestPN[endpoint])
		pnKey := fmt.Sprintf("%s:%d", endpoint, fullPN)
		if st.seenPN[pnKey] {
			st.problems = append(st.problems, ProblemReport{Code: "duplicate_packet", PacketIndex: pkt.index, Detail: endpoint})
			problemCount++
			continue
		}
		st.seenPN[pnKey] = true
		pnCopy := fullPN
		if st.largestPN[endpoint] == nil || fullPN > *st.largestPN[endpoint] {
			st.largestPN[endpoint] = &pnCopy
		}

		params, payloadProblems := parseParams(pkt.payload)
		for _, p := range payloadProblems {
			p.PacketIndex = pkt.index
			st.problems = append(st.problems, p)
			problemCount++
		}
		st.params[endpoint] = append(st.params[endpoint], params...)
		entry := PacketReport{Index: pkt.index, Type: pkt.typ, Endpoint: endpoint, DCID: hex.EncodeToString(pkt.dcid), SCID: hex.EncodeToString(pkt.scid), PacketNumber: &pnCopy}
		if len(pkt.token) > 0 {
			entry.Token = hex.EncodeToString(pkt.token)
		}
		st.packets = append(st.packets, entry)
	}

	keys := make([]string, 0, len(states))
	for key := range states {
		keys = append(keys, key)
	}
	sort.Strings(keys)

	report := Report{Version: "qtrace-1"}
	for _, key := range keys {
		st := states[key]
		cr := ConnectionReport{ConnectionID: key, RetryValid: st.hadRetry && !st.badRetry, PacketCount: len(st.packets), Packets: st.packets, Problems: st.problems}
		cr.TransportParameters.Client = canonicalParams(st.params["client"])
		cr.TransportParameters.Server = canonicalParams(st.params["server"])
		report.Connections = append(report.Connections, cr)
	}
	report.Summary = Summary{TraceCount: 1, PacketCount: len(packets), ConnectionCount: len(report.Connections), ProblemCount: problemCount, Valid: problemCount == 0}
	return report, nil
}

func connectionKey(pkt packet, endpoint string) string {
	if pkt.typ == "retry" || endpoint == "server" {
		return hex.EncodeToString(pkt.dcid)
	}
	return hex.EncodeToString(pkt.scid)
}

func readBundle(data []byte) ([]packet, error) {
	if len(data) < 4 || string(data[:4]) != "QTV1" {
		return nil, errors.New("bad trace magic")
	}
	r := bytes.NewReader(data[4:])
	count, err := readVarint(r)
	if err != nil {
		return nil, err
	}
	packets := make([]packet, 0, count)
	for i := 0; i < int(count); i++ {
		length, err := readVarint(r)
		if err != nil {
			return nil, err
		}
		if length > uint64(r.Len()) {
			return nil, io.ErrUnexpectedEOF
		}
		raw := make([]byte, length)
		if _, err := io.ReadFull(r, raw); err != nil {
			return nil, err
		}
		pkt, err := parsePacket(i, raw)
		if err != nil {
			return nil, fmt.Errorf("packet %d: %w", i, err)
		}
		packets = append(packets, pkt)
	}
	return packets, nil
}

func readVarint(r *bytes.Reader) (uint64, error) {
	first, err := r.ReadByte()
	if err != nil {
		return 0, err
	}
	size := 1 << (first >> 6)
	buf := []byte{first & 0x3f}
	for len(buf) < int(size) {
		b, err := r.ReadByte()
		if err != nil {
			return 0, err
		}
		buf = append(buf, b)
	}
	var value uint64
	for _, b := range buf {
		value = (value << 8) | uint64(b)
	}
	return value, nil
}

func parsePacket(index int, raw []byte) (packet, error) {
	if len(raw) < 7 {
		return packet{}, io.ErrUnexpectedEOF
	}
	first := raw[0]
	if first&0xc0 != 0xc0 {
		return packet{}, errors.New("not a long header")
	}
	typBits := (first >> 4) & 0x03
	r := bytes.NewReader(raw[5:])
	dcid, err := readCID(r)
	if err != nil {
		return packet{}, err
	}
	scid, err := readCID(r)
	if err != nil {
		return packet{}, err
	}
	tokenLen, err := readVarint(r)
	if err != nil {
		return packet{}, err
	}
	if tokenLen > uint64(r.Len()) {
		return packet{}, io.ErrUnexpectedEOF
	}
	token := make([]byte, tokenLen)
	if _, err := io.ReadFull(r, token); err != nil {
		return packet{}, err
	}
	pkt := packet{index: index, raw: raw, dcid: dcid, scid: scid, token: token}
	if typBits == 3 {
		if r.Len() != 16 {
			return packet{}, errors.New("bad retry length")
		}
		pkt.typ = "retry"
		pkt.tag = make([]byte, 16)
		_, _ = io.ReadFull(r, pkt.tag)
		return pkt, nil
	}
	pkt.typ = "initial"
	pkt.pnLen = int(first&0x03) + 1
	length, err := readVarint(r)
	if err != nil {
		return packet{}, err
	}
	if length < uint64(pkt.pnLen) || length > uint64(r.Len()) {
		return packet{}, io.ErrUnexpectedEOF
	}
	pnBytes := make([]byte, pkt.pnLen)
	if _, err := io.ReadFull(r, pnBytes); err != nil {
		return packet{}, err
	}
	for _, b := range pnBytes {
		pkt.truncated = (pkt.truncated << 8) | uint64(b)
	}
	payloadLen := int(length) - pkt.pnLen
	pkt.payload = make([]byte, payloadLen)
	if _, err := io.ReadFull(r, pkt.payload); err != nil {
		return packet{}, err
	}
	if len(pkt.payload) == 0 || (pkt.payload[0] != 1 && pkt.payload[0] != 2) {
		return packet{}, errors.New("bad payload endpoint")
	}
	return pkt, nil
}

func readCID(r *bytes.Reader) ([]byte, error) {
	l, err := r.ReadByte()
	if err != nil {
		return nil, err
	}
	if int(l) > r.Len() {
		return nil, io.ErrUnexpectedEOF
	}
	out := make([]byte, int(l))
	_, err = io.ReadFull(r, out)
	return out, err
}

func reconstructPacketNumber(truncated uint64, pnLen int, largest *int64) int64 {
	if largest == nil {
		return int64(truncated)
	}
	expected := *largest + 1
	window := int64(uint64(1) << (uint(pnLen) * 8))
	half := window / 2
	candidate := (expected & ^(window - 1)) | int64(truncated)
	if candidate <= expected-half && candidate < (1<<62)-window {
		candidate += window
	} else if candidate > expected+half && candidate >= window {
		candidate -= window
	}
	return candidate
}

func validRetry(odcid, raw, tag []byte) bool {
	if len(raw) < 16 || len(tag) != 16 {
		return false
	}
	h := sha256.New()
	h.Write([]byte("retry-v1"))
	h.Write([]byte{byte(len(odcid))})
	h.Write(odcid)
	h.Write(raw[:len(raw)-16])
	sum := h.Sum(nil)
	return bytes.Equal(sum[:16], tag)
}

func parseParams(payload []byte) ([]ParamReport, []ProblemReport) {
	if len(payload) < 2 {
		return nil, nil
	}
	r := bytes.NewReader(payload[1:])
	count, err := readVarint(r)
	if err != nil {
		return nil, nil
	}
	params := make([]ParamReport, 0, count)
	problems := []ProblemReport{}
	seen := map[string]bool{}
	for i := 0; i < int(count); i++ {
		id, err := readVarint(r)
		if err != nil {
			break
		}
		length, err := readVarint(r)
		if err != nil || length > uint64(r.Len()) {
			break
		}
		value := make([]byte, length)
		_, _ = io.ReadFull(r, value)
		key := fmt.Sprintf("0x%04x", id)
		if seen[key] {
			problems = append(problems, ProblemReport{Code: "duplicate_transport_parameter", Detail: key})
		}
		seen[key] = true
		params = append(params, ParamReport{ID: key, Value: hex.EncodeToString(value)})
	}
	return params, problems
}

func canonicalParams(in []ParamReport) []ParamReport {
	if len(in) == 0 {
		return []ParamReport{}
	}
	out := append([]ParamReport(nil), in...)
	sort.SliceStable(out, func(i, j int) bool {
		idi := parseParamID(out[i].ID)
		idj := parseParamID(out[j].ID)
		if idi != idj {
			return idi < idj
		}
		return out[i].Value < out[j].Value
	})
	return out
}

func parseParamID(s string) uint64 {
	var id uint64
	_, _ = fmt.Sscanf(s, "0x%x", &id)
	return id
}

var _ = binary.BigEndian
GO

gofmt -w internal/qtrace/qtrace.go cmd/quicval/main.go
go test ./...
go build -o /app/quicval/bin/quicval /app/quicval/cmd/quicval
/app/quicval/bin/quicval --trace /app/quicval/fixtures/customer-alpha.qtr --out /app/quicval/out/transcript-report.json

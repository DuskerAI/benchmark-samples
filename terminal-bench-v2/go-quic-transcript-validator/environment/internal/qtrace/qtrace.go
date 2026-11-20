package qtrace

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
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
	index   int
	raw     []byte
	typ     string
	version uint32
	dcid    []byte
	scid    []byte
	token   []byte
	pnLen   int
	pn      int64
	payload []byte
	tag     []byte
}

type connState struct {
	id       string
	odcid    []byte
	packets  []PacketReport
	problems []ProblemReport
	params   map[string][]ParamReport
	seenPN   map[string]bool
	largest  map[string]int64
	retryOK  bool
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
	conns := map[string]*connState{}
	order := []string{}
	problems := 0

	for _, p := range packets {
		key := hex.EncodeToString(p.scid)
		endpoint := "client"
		if p.typ == "retry" || (len(p.payload) > 0 && p.payload[0] == 2) {
			key = hex.EncodeToString(p.dcid)
			endpoint = "server"
		}
		if key == "" {
			key = hex.EncodeToString(p.dcid)
		}
		st := conns[key]
		if st == nil {
			st = &connState{id: key, odcid: append([]byte(nil), p.dcid...), params: map[string][]ParamReport{"client": {}, "server": {}}, seenPN: map[string]bool{}, largest: map[string]int64{"client": -1, "server": -1}}
			conns[key] = st
			order = append(order, key)
		}
		if p.typ == "initial" {
			pktKey := fmt.Sprintf("%s:%d", endpoint, p.pn)
			if st.seenPN[pktKey] {
				st.problems = append(st.problems, ProblemReport{Code: "duplicate_packet", PacketIndex: p.index, Detail: endpoint})
				problems++
				continue
			}
			st.seenPN[pktKey] = true
			if p.pn > st.largest[endpoint] {
				st.largest[endpoint] = p.pn
			}
			pn := p.pn
			st.packets = append(st.packets, PacketReport{Index: p.index, Type: p.typ, Endpoint: endpoint, DCID: hex.EncodeToString(p.dcid), SCID: hex.EncodeToString(p.scid), PacketNumber: &pn, Token: hex.EncodeToString(p.token)})
			params, ps := readParams(p.payload)
			for _, pr := range ps {
				pr.PacketIndex = p.index
				st.problems = append(st.problems, pr)
				problems++
			}
			st.params[endpoint] = append(st.params[endpoint], params...)
		} else {
			ok := checkRetry(st.odcid, p.raw, p.tag)
			st.retryOK = ok
			if !ok {
				st.problems = append(st.problems, ProblemReport{Code: "bad_retry_integrity", PacketIndex: p.index, Detail: "retry"})
				problems++
			}
			st.packets = append(st.packets, PacketReport{Index: p.index, Type: p.typ, Endpoint: endpoint, DCID: hex.EncodeToString(p.dcid), SCID: hex.EncodeToString(p.scid), PacketNumber: nil, Token: hex.EncodeToString(p.token)})
		}
	}

	rep := Report{Version: "qtrace-1"}
	sort.Strings(order)
	for _, key := range order {
		st := conns[key]
		cr := ConnectionReport{ConnectionID: key, RetryValid: st.retryOK, PacketCount: len(st.packets), Packets: st.packets, Problems: st.problems}
		cr.TransportParameters.Client = canon(st.params["client"])
		cr.TransportParameters.Server = canon(st.params["server"])
		rep.Connections = append(rep.Connections, cr)
	}
	rep.Summary = Summary{TraceCount: 1, PacketCount: len(packets), ConnectionCount: len(rep.Connections), ProblemCount: problems, Valid: problems == 0}
	return rep, nil
}

func readBundle(data []byte) ([]packet, error) {
	if len(data) < 4 || string(data[:4]) != "QTV1" {
		return nil, errors.New("bad trace magic")
	}
	r := bytes.NewReader(data[4:])
	n, err := readVarint(r)
	if err != nil {
		return nil, err
	}
	out := []packet{}
	for i := 0; i < int(n); i++ {
		l, err := readVarint(r)
		if err != nil {
			return nil, err
		}
		b := make([]byte, l)
		if _, err := r.Read(b); err != nil {
			return nil, err
		}
		p, err := parsePacket(i, b)
		if err != nil {
			continue
		}
		out = append(out, p)
	}
	return out, nil
}

func readVarint(r *bytes.Reader) (uint64, error) {
	b, err := r.ReadByte()
	if err != nil {
		return 0, err
	}
	return uint64(b & 0x3f), nil
}

func parsePacket(index int, b []byte) (packet, error) {
	r := bytes.NewReader(b)
	first, _ := r.ReadByte()
	typBits := (first >> 4) & 3
	p := packet{index: index, raw: b, version: binary.BigEndian.Uint32(b[1:5])}
	r.Seek(5, 0)
	dlen, _ := r.ReadByte()
	p.dcid = make([]byte, dlen)
	r.Read(p.dcid)
	slen, _ := r.ReadByte()
	p.scid = make([]byte, slen)
	r.Read(p.scid)
	if typBits == 3 {
		p.typ = "retry"
		tokLen, _ := readVarint(r)
		p.token = make([]byte, tokLen)
		r.Read(p.token)
		p.tag = b[len(b)-16:]
		return p, nil
	}
	p.typ = "initial"
	p.pnLen = int(first&3) + 1
	tokLen, _ := readVarint(r)
	p.token = make([]byte, tokLen)
	r.Read(p.token)
	plen, _ := readVarint(r)
	pn := make([]byte, p.pnLen)
	r.Read(pn)
	p.pn = int64(0)
	for _, x := range pn {
		p.pn = p.pn<<8 | int64(x)
	}
	payloadLen := int(plen) - p.pnLen
	if payloadLen > 0 {
		p.payload = make([]byte, payloadLen)
		r.Read(p.payload)
	}
	return p, nil
}

func checkRetry(odcid, raw, tag []byte) bool {
	h := sha256.New()
	h.Write([]byte("retry-v1"))
	h.Write(odcid)
	h.Write(raw[:len(raw)-16])
	sum := h.Sum(nil)
	return bytes.Equal(sum[:16], tag)
}

func readParams(payload []byte) ([]ParamReport, []ProblemReport) {
	if len(payload) < 2 {
		return nil, nil
	}
	endpoint := payload[0]
	_ = endpoint
	r := bytes.NewReader(payload[1:])
	n, err := readVarint(r)
	if err != nil {
		return nil, nil
	}
	out := []ParamReport{}
	seen := map[string]bool{}
	problems := []ProblemReport{}
	for i := 0; i < int(n); i++ {
		id, _ := readVarint(r)
		l, _ := readVarint(r)
		v := make([]byte, l)
		r.Read(v)
		key := fmt.Sprintf("0x%04x", id)
		if seen[key] {
			problems = append(problems, ProblemReport{Code: "duplicate_transport_parameter", Detail: key})
		}
		seen[key] = true
		out = append(out, ParamReport{ID: key, Value: fmt.Sprintf("%d", v)})
	}
	return out, problems
}

func canon(in []ParamReport) []ParamReport {
	sort.SliceStable(in, func(i, j int) bool { return in[i].ID < in[j].ID })
	return in
}

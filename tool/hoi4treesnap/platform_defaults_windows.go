//go:build windows

package main

func defaultGamePathHint() string {
	return `C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV`
}

func autodetectGamePathCandidates() []string {
	return nil
}
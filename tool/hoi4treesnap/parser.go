package main

import (
	"bytes"
	"image"
	"log/slog"
	"math"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/macroblock/imed/pkg/ptool"
)

var pdxRule = `
	entry                = '' scopeBody$;

	declr                = lval '=' rval [';'] [@comment];
	declrScope           = lval '=' scope [';'] [@comment];
	comparison           = lval @operators rval [';'] [@comment];
	list                 = @anyType {@anyType} [';']  [@comment];

	lval                 = @var|@date|@int|'"'#@string#'"';
	rval                 = @var|@date|@hex|@percent|@number|'"'#@string#'"';

	scope                = '{' (scopeBody|@empty) ('}'|empty);
	scopeBody            = (@declr|@declrScope|@comparison|@list){@declr|@declrScope|@comparison|@list};
	comment              = '#'#{#!\x0a#!\x0d#!$#anyRune};

	int                  = ['-']digit#{#digit};
	float                = ['-'][int]#'.'#int;
	number               = float|int;
	percent              = number#'%';
	string               = {!'"'#stringChar};
	var                  = symbol#{#symbol};
	date                 = int#'.'#int#'.'#int#['.'#int];
	bool                 = 'yes'|'no';
	hex                  = '0x'#(digit|letter)(digit|letter)(digit|letter)(digit|letter)(digit|letter)(digit|letter)(digit|letter)(digit|letter);
	anyType              = percent|number|'"'#string#'"'|var|date|bool|hex;

	                     = {spaces|@comment};
	spaces               = \x00..\x20;
	anyRune              = \x00..$;
	digit                = '0'..'9';
	letter               = 'a'..'z'|'A'..'Z'|'а'..'я'|'А'..'Я'|\u00c0..\u00d6|\u00d8..\u00f6|\u00f8..\u00ff|\u0100..\u017f|\u0180..\u024f|\u0400..\u04ff|\u0500..\u052f;
	operators            = '>='|'<='|'!='|'=='|'<'|'>';
	symbol               = digit|letter|'_'|':'|'@'|'.'|'-'|'^'|'|'|'['|']'|'?'|\u0027;
	stringChar           = ('\"'|anyRune);
	empty                = '';
`

// pair                 = @key ':' [@number] '"'#@value#'"' [@comment];

func parseFocus(path string) error {
	slog.Debug("parsing focus file", "path", path)
	f, err := readFile(path)
	if err != nil {
		return err
	}

	if len(f) > 0 {
		node, err := parsePdxSource(f)
		if err != nil {
			return err
		}
		_ = node
		// fmt.Println(ptool.TreeToString(node, pdx.ByID))
		err = traverseFocus(node)
		if err != nil {
			return err
		}
	}
	return nil
}

func traverseFocus(root *ptool.TNode) error {
	for _, node := range root.Links {
		nodeType := pdx.ByID(node.Type)
		switch nodeType {
		case "declrScope":
			switch strings.ToLower(node.Links[0].Value) {
			case "focus", "shared_focus":
				var f Focus
				f.AllowBranch = true
				f.Available = true
				var err error
				var n float64
				for _, link := range node.Links {
					nodeType := pdx.ByID(link.Type)
					switch nodeType {
					case "declr":
						switch strings.ToLower(link.Links[0].Value) {
						case "id":
							f.ID = link.Links[1].Value
							locList = append(locList, link.Links[1].Value)
						case "icon":
							f.Icon = link.Links[1].Value
							gfxList = append(gfxList, "\""+link.Links[1].Value+"\"")
						case "text":
							f.Text = link.Links[1].Value
							locList = append(locList, link.Links[1].Value)
						case "x":
							n, err = strconv.ParseFloat(link.Links[1].Value, 64)
							if err != nil {
								return err
							}
							f.X = int(math.Trunc(n))
						case "y":
							n, err = strconv.ParseFloat(link.Links[1].Value, 64)
							if err != nil {
								return err
							}
							f.Y = int(math.Trunc(n))
						case "relative_position_id":
							f.RelativePositionID = link.Links[1].Value
						}
					case "declrScope":
						switch strings.ToLower(link.Links[0].Value) {
						case "prerequisite":
							var p []string
							for _, link := range link.Links {
								nodeType := pdx.ByID(link.Type)
								switch nodeType {
								case "declr":
									switch strings.ToLower(link.Links[0].Value) {
									case "focus":
										p = append(p, link.Links[1].Value)
									}
								}
							}
							f.Prerequisite = append(f.Prerequisite, p)
						case "mutually_exclusive":
							for _, link := range link.Links {
								nodeType := pdx.ByID(link.Type)
								switch nodeType {
								case "declr":
									switch strings.ToLower(link.Links[0].Value) {
									case "focus":
										f.MutuallyExclusive = append(f.MutuallyExclusive, link.Links[1].Value)
									}
								}
							}
						case "allow_branch":
							for _, link := range link.Links {
								nodeType := pdx.ByID(link.Type)
								switch nodeType {
								case "declr":
									switch strings.ToLower(link.Links[0].Value) {
									case "always":
										if strings.ToLower(link.Links[1].Value) == "no" {
											f.AllowBranch = false
										}
									case "has_country_flag":
										if strings.ToLower(link.Links[1].Value) == "romanov_enabled" { // Poland tree workaround
											f.AllowBranch = false
										}
									}
								case "declrScope":
									switch strings.ToLower(link.Links[0].Value) {
									case "not":
										for _, link := range link.Links {
											nodeType := pdx.ByID(link.Type)
											switch nodeType {
											case "declr":
												switch strings.ToLower(link.Links[0].Value) {
												case "has_dlc":
													f.AllowBranch = false
												}
											}
										}
									}
								}
							}
						case "available":
							for _, link := range link.Links {
								if len(link.Links) > 0 {
									f.Available = false
								}
							}
						}
					}
				}
				focusMap[f.ID] = f
			default:
				err := traverseFocus(node)
				if err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func parseGUI(path string) error {
	fPath := filepath.Join(path, "interface", "nationalfocusview.gui")

	f, err := readFile(fPath)
	if err != nil {
		return err
	}

	if len(f) > 0 {
		node, err := parsePdxSource(f)
		if err != nil {
			return err
		}
		_ = node
		// fmt.Println(ptool.TreeToString(node, pdx.ByID))
		err = traverseGUI(node)
		if err != nil {
			return err
		}
	}
	return nil
}

func parsePdxSource(input string) (*ptool.TNode, error) {
	normalized := normalizePdxSource(input)
	node, err := pdx.Parse(normalized)
	if err != nil && strings.Contains(err.Error(), "unexpected '}'") {
		normalized = stripUnmatchedClosingBraces(normalized)
		node, err = pdx.Parse(normalized)
	}
	if err != nil {
		return nil, err
	}
	return node, nil
}

func normalizePdxSource(input string) string {
	if bytes.HasPrefix([]byte(input), utf8bom) {
		input = string(bytes.TrimPrefix([]byte(input), utf8bom))
	}
	return stripNumericPercentSuffixes(input)
}

func stripNumericPercentSuffixes(input string) string {
	var b strings.Builder
	b.Grow(len(input))

	inString := false
	inComment := false
	escaped := false
	lastNonSpace := rune(0)

	for _, r := range input {
		switch {
		case inComment:
			b.WriteRune(r)
			if r == '\n' || r == '\r' {
				inComment = false
			}
			continue
		case inString:
			b.WriteRune(r)
			if escaped {
				escaped = false
				continue
			}
			if r == '\\' {
				escaped = true
				continue
			}
			if r == '"' {
				inString = false
			}
			continue
		}

		if r == '#' {
			inComment = true
			b.WriteRune(r)
			continue
		}
		if r == '"' {
			inString = true
			b.WriteRune(r)
			lastNonSpace = r
			continue
		}
		if r == '%' && lastNonSpace >= '0' && lastNonSpace <= '9' {
			continue
		}

		b.WriteRune(r)
		if r > ' ' {
			lastNonSpace = r
		}
	}

	return b.String()
}

func stripUnmatchedClosingBraces(input string) string {
	var b strings.Builder
	b.Grow(len(input))

	depth := 0
	inString := false
	inComment := false
	escaped := false

	for _, r := range input {
		switch {
		case inComment:
			b.WriteRune(r)
			if r == '\n' || r == '\r' {
				inComment = false
			}
			continue
		case inString:
			b.WriteRune(r)
			if escaped {
				escaped = false
				continue
			}
			if r == '\\' {
				escaped = true
				continue
			}
			if r == '"' {
				inString = false
			}
			continue
		}

		if r == '#' {
			inComment = true
			b.WriteRune(r)
			continue
		}
		if r == '"' {
			inString = true
			b.WriteRune(r)
			continue
		}
		if r == '{' {
			depth++
			b.WriteRune(r)
			continue
		}
		if r == '}' {
			if depth == 0 {
				continue
			}
			depth--
			b.WriteRune(r)
			continue
		}
		b.WriteRune(r)
	}

	return b.String()
}

func traverseGUI(root *ptool.TNode) error {
	var err error
	for _, node := range root.Links {
		nodeType := pdx.ByID(node.Type)
		switch nodeType {
		case "declrScope":
			switch strings.ToLower(node.Links[0].Value) {
			case "containerwindowtype":
				nfv := false
				nfi := false
				nfl := false
				nfei := false
				for _, link := range node.Links {
					if pdx.ByID(link.Type) == "declr" {
						if strings.ToLower(link.Links[0].Value) == "name" && link.Links[1].Value == "nationalfocusview" {
							nfv = true
						}
						if strings.ToLower(link.Links[0].Value) == "name" && link.Links[1].Value == "national_focus_item" {
							nfi = true
						}
						if strings.ToLower(link.Links[0].Value) == "name" && link.Links[1].Value == "national_focus_link" {
							nfl = true
						}
						if strings.ToLower(link.Links[0].Value) == "name" && link.Links[1].Value == "national_focus_exclusive_item" {
							nfei = true
						}
					}
				}

				switch {
				case nfv:
					for _, link := range node.Links {
						if len(link.Links) > 0 {
							switch strings.ToLower(link.Links[0].Value) {
							case "instanttextboxtype":
								var t InstantTextboxType
								for _, link := range link.Links {
									if len(link.Links) > 0 {
										switch strings.ToLower(link.Links[0].Value) {
										case "name":
											t.Name = link.Links[1].Value
										case "position":
											for _, link := range link.Links {
												nodeType := pdx.ByID(link.Type)
												switch nodeType {
												case "declr":
													switch strings.ToLower(link.Links[0].Value) {
													case "x":
														t.Position.X, err = strconv.Atoi(link.Links[1].Value)
														if err != nil {
															return err
														}
													case "y":
														t.Position.Y, err = strconv.Atoi(link.Links[1].Value)
														if err != nil {
															return err
														}
													}
												}
											}
										case "font":
											t.Font = link.Links[1].Value
											gfxList = append(gfxList, "\""+link.Links[1].Value+"\"")
										case "text":
											t.Text = link.Links[1].Value
										case "maxwidth":
											t.MaxWidth, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										case "maxheight":
											t.MaxHeight, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										case "format":
											t.Format = link.Links[1].Value
										case "vertical_alignment":
											t.VerticalAlignment = link.Links[1].Value
										}
									}
								}
								if t.Name == "national_focus_title" {
									gui.NationalFocusTitle = t
								}
							}
						}
					}

				case nfi:
					for _, link := range node.Links {
						if len(link.Links) > 0 {
							switch strings.ToLower(link.Links[0].Value) {
							case "name":
								gui.NationalFocusItem.Name = link.Links[1].Value
							case "position":
								for _, link := range link.Links {
									nodeType := pdx.ByID(link.Type)
									switch nodeType {
									case "declr":
										switch strings.ToLower(link.Links[0].Value) {
										case "x":
											gui.NationalFocusItem.Position.X, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										case "y":
											gui.NationalFocusItem.Position.Y, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										}
									}
								}
							case "size":
								for _, link := range link.Links {
									nodeType := pdx.ByID(link.Type)
									switch nodeType {
									case "declr":
										switch strings.ToLower(link.Links[0].Value) {
										case "width":
											gui.NationalFocusItem.Width, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										case "height":
											gui.NationalFocusItem.Height, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										}
									}
								}
							case "buttontype":
								var button ButtonType
								for _, link := range link.Links {
									if len(link.Links) > 0 {
										switch strings.ToLower(link.Links[0].Value) {
										case "name":
											button.Name = link.Links[1].Value
										case "position":
											for _, link := range link.Links {
												nodeType := pdx.ByID(link.Type)
												switch nodeType {
												case "declr":
													switch strings.ToLower(link.Links[0].Value) {
													case "x":
														button.Position.X, err = strconv.Atoi(link.Links[1].Value)
														if err != nil {
															return err
														}
													case "y":
														button.Position.Y, err = strconv.Atoi(link.Links[1].Value)
														if err != nil {
															return err
														}
													}
												}
											}
										case "spritetype":
											button.SpriteType = link.Links[1].Value
											gfxList = append(gfxList, "\""+link.Links[1].Value+"\"")
										case "quadtexturesprite":
											button.SpriteType = link.Links[1].Value
											gfxList = append(gfxList, "\""+link.Links[1].Value+"\"")
										case "centerposition":
											button.CenterPosition = link.Links[1].Value
										case "orientation":
											button.Orientation = link.Links[1].Value
										}
									}
								}
								switch strings.ToLower(button.Name) {
								case "bg":
									gui.BG = button
								case "symbol":
									gui.Symbol = button
								}
							case "instanttextboxtype":
								name := false
								for _, link := range link.Links {
									if pdx.ByID(link.Type) == "declr" {
										if strings.ToLower(link.Links[0].Value) == "name" && link.Links[1].Value == "name" {
											name = true
										}
									}
								}
								if name {
									for _, link := range link.Links {
										if len(link.Links) > 0 {
											switch strings.ToLower(link.Links[0].Value) {
											case "name":
												gui.Name.Name = link.Links[1].Value
											case "position":
												for _, link := range link.Links {
													nodeType := pdx.ByID(link.Type)
													switch nodeType {
													case "declr":
														switch strings.ToLower(link.Links[0].Value) {
														case "x":
															gui.Name.Position.X, err = strconv.Atoi(link.Links[1].Value)
															if err != nil {
																return err
															}
														case "y":
															gui.Name.Position.Y, err = strconv.Atoi(link.Links[1].Value)
															if err != nil {
																return err
															}
														}
													}
												}
											case "font":
												gui.Name.Font = link.Links[1].Value
												gfxList = append(gfxList, "\""+link.Links[1].Value+"\"")
											case "text":
												gui.Name.Text = link.Links[1].Value
											case "maxwidth":
												gui.Name.MaxWidth, err = strconv.Atoi(link.Links[1].Value)
												if err != nil {
													return err
												}
											case "maxheight":
												gui.Name.MaxHeight, err = strconv.Atoi(link.Links[1].Value)
												if err != nil {
													return err
												}
											case "format":
												gui.Name.Format = link.Links[1].Value
											case "vertical_alignment":
												gui.Name.VerticalAlignment = link.Links[1].Value
											}
										}
									}
								}
							}
						}
					}

				case nfl:
					for _, link := range node.Links {
						if len(link.Links) > 0 {
							switch strings.ToLower(link.Links[0].Value) {
							case "name":
								gui.NationalFocusLink.Name = link.Links[1].Value
							case "position":
								for _, link := range link.Links {
									nodeType := pdx.ByID(link.Type)
									switch nodeType {
									case "declr":
										switch strings.ToLower(link.Links[0].Value) {
										case "x":
											gui.NationalFocusLink.Position.X, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										case "y":
											gui.NationalFocusLink.Position.Y, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										}
									}
								}
							case "size":
								for _, link := range link.Links {
									nodeType := pdx.ByID(link.Type)
									switch nodeType {
									case "declr":
										switch strings.ToLower(link.Links[0].Value) {
										case "width":
											gui.NationalFocusLink.Width, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										case "height":
											gui.NationalFocusLink.Height, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										}
									}
								}
							case "icontype":
								var icon IconType
								for _, link := range link.Links {
									if len(link.Links) > 0 {
										switch strings.ToLower(link.Links[0].Value) {
										case "name":
											icon.Name = link.Links[1].Value
										case "position":
											for _, link := range link.Links {
												nodeType := pdx.ByID(link.Type)
												switch nodeType {
												case "declr":
													switch strings.ToLower(link.Links[0].Value) {
													case "x":
														icon.Position.X, err = strconv.Atoi(link.Links[1].Value)
														if err != nil {
															return err
														}
													case "y":
														icon.Position.Y, err = strconv.Atoi(link.Links[1].Value)
														if err != nil {
															return err
														}
													}
												}
											}
										case "spritetype":
											icon.SpriteType = link.Links[1].Value
											gfxList = append(gfxList, "\""+link.Links[1].Value+"\"")
										case "frame":
											icon.Frame, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										}
									}
								}
								if strings.ToLower(icon.Name) == "link" {
									gui.Link = icon
								}
							}
						}
					}

				case nfei:
					for _, link := range node.Links {
						if len(link.Links) > 0 {
							switch strings.ToLower(link.Links[0].Value) {
							case "name":
								gui.NationalFocusExclusiveItem.Name = link.Links[1].Value
							case "position":
								for _, link := range link.Links {
									nodeType := pdx.ByID(link.Type)
									switch nodeType {
									case "declr":
										switch strings.ToLower(link.Links[0].Value) {
										case "x":
											gui.NationalFocusExclusiveItem.Position.X, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										case "y":
											gui.NationalFocusExclusiveItem.Position.Y, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										}
									}
								}
							case "size":
								for _, link := range link.Links {
									nodeType := pdx.ByID(link.Type)
									switch nodeType {
									case "declr":
										switch strings.ToLower(link.Links[0].Value) {
										case "width":
											gui.NationalFocusExclusiveItem.Width, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										case "height":
											gui.NationalFocusExclusiveItem.Height, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										}
									}
								}
							case "icontype":
								var icon IconType
								for _, link := range link.Links {
									if len(link.Links) > 0 {
										switch strings.ToLower(link.Links[0].Value) {
										case "name":
											icon.Name = link.Links[1].Value
										case "position":
											for _, link := range link.Links {
												nodeType := pdx.ByID(link.Type)
												switch nodeType {
												case "declr":
													switch strings.ToLower(link.Links[0].Value) {
													case "x":
														icon.Position.X, err = strconv.Atoi(link.Links[1].Value)
														if err != nil {
															return err
														}
													case "y":
														icon.Position.Y, err = strconv.Atoi(link.Links[1].Value)
														if err != nil {
															return err
														}
													}
												}
											}
										case "spritetype":
											icon.SpriteType = link.Links[1].Value
											gfxList = append(gfxList, "\""+link.Links[1].Value+"\"")
										case "frame":
											icon.Frame, err = strconv.Atoi(link.Links[1].Value)
											if err != nil {
												return err
											}
										}
									}
								}
								switch strings.ToLower(icon.Name) {
								case "link1":
									gui.Link1 = icon
								case "link2":
									gui.Link2 = icon
								case "left":
									gui.Left = icon
								case "right":
									gui.Right = icon
								case "mid":
									gui.Mid = icon
								}
							}
						}
					}
				}

			case "positiontype":
				var name string
				var pos image.Point
				for _, link := range node.Links {
					if len(link.Links) > 0 {
						switch strings.ToLower(link.Links[0].Value) {
						case "name":
							name = link.Links[1].Value
						case "position":
							for _, link := range link.Links {
								nodeType := pdx.ByID(link.Type)
								switch nodeType {
								case "declr":
									switch strings.ToLower(link.Links[0].Value) {
									case "x":
										pos.X, err = strconv.Atoi(link.Links[1].Value)
										if err != nil {
											return err
										}
									case "y":
										pos.Y, err = strconv.Atoi(link.Links[1].Value)
										if err != nil {
											return err
										}
									}
								}
							}
						}
					}
				}
				switch strings.ToLower(name) {
				case "focus_spacing":
					gui.FocusSpacing = pos
				case "link_spacing":
					gui.LinkSpacing = pos
				case "link_offsets":
					gui.LinkOffsets = pos
				case "link_begin":
					gui.LinkBegin = pos
				case "link_end":
					gui.LinkEnd = pos
				case "exclusive_offset":
					gui.ExclusiveOffset = pos
				case "exclusive_offset_left":
					gui.ExclusiveOffsetLeft = pos
				case "exclusive_positioning":
					gui.ExclusivePositioning = pos
				}

			default:
				err := traverseGUI(node)
				if err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func parseGFX(path string, i int) error {
	gfxFiles, err := WalkMatchExt(filepath.Join(path, "interface"), ".gfx")
	if err != nil {
		return err
	}
	for _, fPath := range gfxFiles {
		f, err := readFile(fPath)
		if err != nil {
			return err
		}

		if stringContainsSlice(f, gfxList) {
			slog.Debug("parsing GFX file", "path", fPath)
			if len(f) > 0 {
				node, err := parsePdxSource(f)
				if err != nil {
					return err
				}
				_ = node
				// fmt.Println(ptool.TreeToString(node, pdx.ByID))
				err = traverseGFX(node, path)
				if err != nil {
					return err
				}
			}
		}
		addProgress(0.4 / float64(i) / float64(len(gfxFiles)))
	}
	return nil
}

func traverseGFX(root *ptool.TNode, path string) error {
	var err error
	for _, node := range root.Links {
		nodeType := pdx.ByID(node.Type)
		switch nodeType {
		case "declrScope":
			switch strings.ToLower(node.Links[0].Value) {
			case "spritetype", "corneredtilespritetype":
				var s SpriteType
				for _, link := range node.Links {
					nodeType := pdx.ByID(link.Type)
					switch nodeType {
					case "declr":
						switch strings.ToLower(link.Links[0].Value) {
						case "name":
							s.Name = link.Links[1].Value
						case "texturefile":
							s.TextureFile = filepath.Join(path, link.Links[1].Value)
						case "noofframes":
							s.NoOfFrames, err = strconv.Atoi(link.Links[1].Value)
							if err != nil {
								return err
							}
						}
					}
				}
				gfxMap[s.Name] = s
			case "bitmapfont":
				var b BitmapFont
				for _, link := range node.Links {
					nodeType := pdx.ByID(link.Type)
					switch nodeType {
					case "declr":
						switch strings.ToLower(link.Links[0].Value) {
						case "name":
							b.Name = link.Links[1].Value
						case "path":
							b.Path = filepath.Join(path, link.Links[1].Value)
						}
					case "declrScope":
						switch strings.ToLower(link.Links[0].Value) {
						case "fontfiles":
							for _, link := range link.Links {
								nodeType := pdx.ByID(link.Type)
								switch nodeType {
								case "list":
									for _, link := range link.Links {
										nodeType := pdx.ByID(link.Type)
										switch nodeType {
										case "anyType":
											b.Fontfiles = append(b.Fontfiles, filepath.Join(path, trimQuotes(link.Value)))
										}

									}
								}
							}
						}
					}
				}
				if len(b.Fontfiles) < 1 && b.Path != "" {
					b.Fontfiles = append(b.Fontfiles, b.Path)
				}
				fontMap[b.Name] = b
			default:
				err = traverseGFX(node, path)
				if err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func parseLoc(path string, i int) error {
	// WalkMatchExt already recurses into sub-directories (including replace/).
	locFiles, err := WalkMatchExt(filepath.Join(path, "localisation"), ".yml")
	if err != nil {
		return err
	}

	for _, lPath := range locFiles {
		f, err := readFile(lPath)
		if err != nil {
			return err
		}
		if stringContainsSlice(f, locList) {
			if len(f) > 0 {
				// Remove utf-8 bom if found.
				if bytes.HasPrefix([]byte(f), utf8bom) {
					f = string(bytes.TrimPrefix([]byte(f), utf8bom))
				}

				// Skip file if it contains a wrong language.
				if !strings.HasPrefix(strings.TrimSpace(f), language) {
					continue
				}

				slog.Debug("parsing localisation file", "path", lPath)
				before := len(locMap[language])
				parseLocFile(f)
				slog.Debug("loaded localisation entries", "path", lPath, "new", len(locMap[language])-before)
			}
		}
		addProgress(0.4 / float64(i) / float64(len(locFiles)))
	}
	return nil
}

// parseLocFile parses a HOI4 localisation YML file using a fast line-by-line
// scanner instead of the PEG parser, which has O(n²) complexity for large files.
// Format per line (after the language header):
//
//	KEY:NUMBER "VALUE"   (number is optional)
//	KEY: "VALUE"
func parseLocFile(content string) {
	lang := "l_english"
	first := true
	for _, rawLine := range strings.Split(content, "\n") {
		line := strings.TrimRight(rawLine, "\r")

		if first {
			// First non-empty, non-comment line is the language declaration.
			trimmed := strings.TrimSpace(line)
			if trimmed == "" || strings.HasPrefix(trimmed, "#") {
				continue
			}
			if idx := strings.Index(trimmed, ":"); idx > 0 {
				lang = trimmed[:idx]
				if _, ok := locMap[lang]; !ok {
					locMap[lang] = make(map[string]Localisation)
				}
			}
			first = false
			continue
		}

		// Skip blank lines and comments.
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}

		// Find the key (everything up to the first ':').
		colonIdx := strings.Index(trimmed, ":")
		if colonIdx <= 0 {
			continue
		}
		key := trimmed[:colonIdx]
		// Key must consist only of word characters; skip scope/other lines.
		if !isLocKey(key) {
			continue
		}

		rest := strings.TrimSpace(trimmed[colonIdx+1:])

		// Skip optional numeric version field (e.g. "0 ").
		if len(rest) > 0 && rest[0] >= '0' && rest[0] <= '9' {
			spaceIdx := strings.IndexByte(rest, ' ')
			if spaceIdx < 0 {
				continue
			}
			rest = strings.TrimSpace(rest[spaceIdx+1:])
		}

		// Value must start with a double-quote.
		if len(rest) == 0 || rest[0] != '"' {
			continue
		}
		// Strip the leading quote; strip trailing quote if present.
		val := rest[1:]
		if len(val) > 0 && val[len(val)-1] == '"' {
			val = val[:len(val)-1]
		}

		locMap[lang][key] = Localisation{Key: key, Value: val}
	}
}

// isLocKey returns true if s looks like a valid HOI4 localisation key.
func isLocKey(s string) bool {
	if s == "" {
		return false
	}
	for _, c := range s {
		if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
			(c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-' || c == '@') {
			return false
		}
	}
	return true
}

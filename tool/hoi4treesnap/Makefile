APP_NAME := hoi4treesnap
VERSION   ?= 0.5.12
DIST_DIR  := dist
LINUX_BINARY   := $(DIST_DIR)/$(APP_NAME)-linux-amd64
WINDOWS_BINARY := $(DIST_DIR)/$(APP_NAME)-windows-amd64.exe
DEB_PACKAGE    := $(DIST_DIR)/$(APP_NAME)_$(VERSION)_amd64.deb
DEB_STAGING    := $(DIST_DIR)/.deb-staging
GO ?= go

.PHONY: build-linux build-windows build-deb test clean

build-linux:
	mkdir -p $(DIST_DIR)
	CGO_ENABLED=1 GOOS=linux GOARCH=amd64 $(GO) build -trimpath -ldflags="-s -w" -o $(LINUX_BINARY) .

build-deb: build-linux
	rm -rf $(DEB_STAGING)
	mkdir -p $(DEB_STAGING)/DEBIAN $(DEB_STAGING)/usr/bin
	cp $(LINUX_BINARY) $(DEB_STAGING)/usr/bin/$(APP_NAME)
	chmod 0755 $(DEB_STAGING)/usr/bin/$(APP_NAME)
	printf 'Package: %s\nVersion: %s\nSection: utils\nPriority: optional\nArchitecture: amd64\nMaintainer: cpntodd <cpntodd@users.noreply.github.com>\nDescription: Hearts of Iron IV focus tree screenshot generator\n' \
		$(APP_NAME) $(VERSION) > $(DEB_STAGING)/DEBIAN/control
	dpkg-deb --build --root-owner-group $(DEB_STAGING) $(DEB_PACKAGE)
	rm -rf $(DEB_STAGING)

build-windows:
	mkdir -p $(DIST_DIR)
	@if [ "$$(go env GOHOSTOS)" != "windows" ]; then \
		echo "build-windows is intended for a native Windows environment or the GitHub Actions windows-latest runner."; \
		exit 1; \
	fi
	GOOS=windows GOARCH=amd64 $(GO) build -trimpath -ldflags="-s -w" -o $(WINDOWS_BINARY) .

test:
	$(GO) test ./...

clean:
	rm -rf $(DIST_DIR)
#import <Cocoa/Cocoa.h>
#import <CoreAudio/CoreAudio.h>
#import <AudioToolbox/AudioToolbox.h>
#import <sys/mman.h>
#import <fcntl.h>
#import <unistd.h>
#import <math.h>

#include "tascam_shm.h"

#define WIN_W 980
#define WIN_H 620

// Buffer Sizes supported by TASCAM US-1800 driver
static const UInt32 g_bufSizes[] = {16, 32, 64, 128, 256, 512, 1024, 2048};
#define NUM_BUF_SIZES 8

static inline NSColor *ColorHex(uint32_t hex, CGFloat alpha) {
    CGFloat r = ((hex >> 16) & 0xFF) / 255.0;
    CGFloat g = ((hex >> 8) & 0xFF) / 255.0;
    CGFloat b = (hex & 0xFF) / 255.0;
    return [NSColor colorWithCalibratedRed:r green:g blue:b alpha:alpha];
}

static NSColor *g_colLedRed = nil;
static NSColor *g_colLedOrange = nil;
static NSColor *g_colLedYellow = nil;
static NSColor *g_colLedGreen1 = nil;
static NSColor *g_colLedGreen2 = nil;
static NSColor *g_colLedOffFill = nil;
static NSColor *g_colLedOffStroke = nil;
static NSColor *g_colClipOffFill = nil;
static NSColor *g_colClipOffStroke = nil;
static NSColor *g_colWhite = nil;

static void InitStaticColors(void) {
    if (g_colLedRed != nil) return;
    g_colLedRed = ColorHex(0xff1744, 1.0);
    g_colLedOrange = ColorHex(0xff9100, 1.0);
    g_colLedYellow = ColorHex(0xffd600, 1.0);
    g_colLedGreen1 = ColorHex(0x00e676, 1.0);
    g_colLedGreen2 = ColorHex(0x00c853, 1.0);
    g_colLedOffFill = ColorHex(0x161e2a, 1.0);
    g_colLedOffStroke = ColorHex(0x243044, 1.0);
    g_colClipOffFill = ColorHex(0x280e14, 1.0);
    g_colClipOffStroke = ColorHex(0x44141c, 1.0);
    g_colWhite = [NSColor whiteColor];
}

static inline float DbToNorm(float db) {
    if (db <= -60.0f) return 0.0f;
    if (db >= 0.0f) return 1.0f;
    if (db > -6.0f) {
        return 0.80f + 0.20f * ((db + 6.0f) / 6.0f);
    } else if (db > -18.0f) {
        return 0.55f + 0.25f * ((db + 18.0f) / 12.0f);
    } else if (db > -36.0f) {
        return 0.25f + 0.30f * ((db + 36.0f) / 18.0f);
    } else {
        return 0.25f * ((db + 60.0f) / 24.0f);
    }
}

static inline float PeakToDb(float pk) {
    if (pk < 0.00001f) return -60.0f;
    float db = 20.0f * log10f(pk);
    return db < -60.0f ? -60.0f : (db > 0.0f ? 0.0f : db);
}

#pragma mark - CoreAudio HAL Helper

static AudioDeviceID FindTascamDeviceID(void) {
    AudioObjectPropertyAddress addr = {
        kAudioHardwarePropertyDevices,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    UInt32 dataSize = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &addr, 0, NULL, &dataSize) != noErr) return kAudioObjectUnknown;
    UInt32 count = dataSize / sizeof(AudioDeviceID);
    AudioDeviceID *devs = (AudioDeviceID *)malloc(dataSize);
    if (!devs) return kAudioObjectUnknown;
    AudioObjectGetPropertyData(kAudioObjectSystemObject, &addr, 0, NULL, &dataSize, devs);
    
    AudioDeviceID found = kAudioObjectUnknown;
    for (UInt32 i = 0; i < count; i++) {
        CFStringRef name = NULL;
        UInt32 nameSize = sizeof(CFStringRef);
        AudioObjectPropertyAddress nameAddr = {
            kAudioObjectPropertyName,
            kAudioObjectPropertyScopeGlobal,
            kAudioObjectPropertyElementMain
        };
        if (AudioObjectGetPropertyData(devs[i], &nameAddr, 0, NULL, &nameSize, &name) == noErr && name) {
            char buf[256];
            CFStringGetCString(name, buf, sizeof(buf), kCFStringEncodingUTF8);
            CFRelease(name);
            if (strstr(buf, "TASCAM US-1800") || strstr(buf, "US-1800")) {
                found = devs[i];
                break;
            }
        }
    }
    free(devs);
    return found;
}

static UInt32 GetHardwareBufferSize(AudioDeviceID devId) {
    if (devId == kAudioObjectUnknown) return 0;
    UInt32 buf = 0;
    UInt32 sz = sizeof(buf);
    AudioObjectPropertyAddress addr = {
        kAudioDevicePropertyBufferFrameSize,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    if (AudioObjectGetPropertyData(devId, &addr, 0, NULL, &sz, &buf) == noErr) {
        return buf;
    }
    return 0;
}

static BOOL SetHardwareBufferSize(AudioDeviceID devId, UInt32 newBuf) {
    if (devId == kAudioObjectUnknown) return NO;
    AudioObjectPropertyAddress addrGlobal = {
        kAudioDevicePropertyBufferFrameSize,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    AudioObjectPropertyAddress addrIn = {
        kAudioDevicePropertyBufferFrameSize,
        kAudioObjectPropertyScopeInput,
        kAudioObjectPropertyElementMain
    };
    AudioObjectPropertyAddress addrOut = {
        kAudioDevicePropertyBufferFrameSize,
        kAudioObjectPropertyScopeOutput,
        kAudioObjectPropertyElementMain
    };
    OSStatus err1 = AudioObjectSetPropertyData(devId, &addrGlobal, 0, NULL, sizeof(newBuf), &newBuf);
    OSStatus err2 = AudioObjectSetPropertyData(devId, &addrIn, 0, NULL, sizeof(newBuf), &newBuf);
    OSStatus err3 = AudioObjectSetPropertyData(devId, &addrOut, 0, NULL, sizeof(newBuf), &newBuf);
    return (err1 == noErr || err2 == noErr || err3 == noErr);
}

static Float64 GetHardwareSampleRate(AudioDeviceID devId) {
    if (devId == kAudioObjectUnknown) return 0.0;
    Float64 rate = 0.0;
    UInt32 sz = sizeof(rate);
    AudioObjectPropertyAddress addr = {
        kAudioDevicePropertyNominalSampleRate,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    if (AudioObjectGetPropertyData(devId, &addr, 0, NULL, &sz, &rate) == noErr) {
        return rate;
    }
    return 0.0;
}

#pragma mark - Custom UI Views

// 1. Top Header View
@interface HeaderView : NSView
@property (nonatomic, assign) BOOL isOnline;
@property (nonatomic, assign) BOOL isStreaming;
@end

@implementation HeaderView
- (void)drawRect:(NSRect)dirtyRect {
    NSRect bounds = self.bounds;
    [ColorHex(0x151922, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:bounds xRadius:6 yRadius:6] fill];
    [ColorHex(0x232a3b, 1.0) setStroke];
    [[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(bounds, 0.5, 0.5) xRadius:6 yRadius:6] stroke];

    // Model title
    NSDictionary *tAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:14.0] ?: [NSFont boldSystemFontOfSize:14.0],
        NSForegroundColorAttributeName: [NSColor whiteColor]
    };
    [@"TASCAM US-1800" drawAtPoint:NSMakePoint(14, bounds.size.height - 24) withAttributes:tAttr];

    NSDictionary *subAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica" size:10.0] ?: [NSFont systemFontOfSize:10.0],
        NSForegroundColorAttributeName: ColorHex(0x94a3b8, 1.0)
    };
    [@"16-In / 4-Out Apple Silicon Native  •  Userspace IOKit HAL  •  Ultra-Low Latency" drawAtPoint:NSMakePoint(14, bounds.size.height - 40) withAttributes:subAttr];

    // Right-side Badges
    CGFloat curX = bounds.size.width - 14;
    
    // Status Pill
    NSString *stText = _isStreaming ? @"● STREAMING (ACTIVE)" : (_isOnline ? @"● HARDWARE ONLINE" : @"● DISCONNECTED");
    NSColor *stBg = (_isOnline || _isStreaming) ? ColorHex(0x064e3b, 1.0) : ColorHex(0x7f1d1d, 1.0);
    NSColor *stFg = (_isOnline || _isStreaming) ? ColorHex(0x34d399, 1.0) : ColorHex(0xf87171, 1.0);
    NSDictionary *stAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:9.0] ?: [NSFont boldSystemFontOfSize:9.0],
        NSForegroundColorAttributeName: stFg
    };
    NSSize stSize = [stText sizeWithAttributes:stAttr];
    CGFloat pillW = stSize.width + 16;
    curX -= pillW;
    NSRect stRect = NSMakeRect(curX, (bounds.size.height - 22)/2.0, pillW, 22);
    [stBg setFill];
    [[NSBezierPath bezierPathWithRoundedRect:stRect xRadius:11 yRadius:11] fill];
    [stText drawAtPoint:NSMakePoint(curX + 8, stRect.origin.y + 5.5) withAttributes:stAttr];

    // CoreAudio HAL Pill
    curX -= 8;
    NSString *halText = @"COREAUDIO HAL";
    NSDictionary *badgeAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.5] ?: [NSFont boldSystemFontOfSize:8.5],
        NSForegroundColorAttributeName: ColorHex(0x94a3b8, 1.0)
    };
    NSSize halSize = [halText sizeWithAttributes:badgeAttr];
    CGFloat halW = halSize.width + 14;
    curX -= halW;
    NSRect halRect = NSMakeRect(curX, (bounds.size.height - 20)/2.0, halW, 20);
    [ColorHex(0x1e2434, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:halRect xRadius:4 yRadius:4] fill];
    [halText drawAtPoint:NSMakePoint(curX + 7, halRect.origin.y + 4.5) withAttributes:badgeAttr];

    // USB 2.0 Pill
    curX -= 6;
    NSString *usbText = @"USB 2.0 (480M)";
    NSSize usbSize = [usbText sizeWithAttributes:badgeAttr];
    CGFloat usbW = usbSize.width + 14;
    curX -= usbW;
    NSRect usbRect = NSMakeRect(curX, (bounds.size.height - 20)/2.0, usbW, 20);
    [ColorHex(0x1e2434, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:usbRect xRadius:4 yRadius:4] fill];
    [usbText drawAtPoint:NSMakePoint(curX + 7, usbRect.origin.y + 4.5) withAttributes:badgeAttr];
}
@end

// 2. Telemetry Deck View
@interface TelemetryDeckView : NSView
@property (nonatomic, assign) UInt32 sampleRate;
@property (nonatomic, assign) UInt32 bufferSize;
@property (nonatomic, assign) float estRtlMs;
@property (nonatomic, assign) BOOL isOnline;
@property (nonatomic, assign) BOOL isConfirmed;
@end

@implementation TelemetryDeckView
- (void)drawRect:(NSRect)dirtyRect {
    NSRect bounds = self.bounds;
    CGFloat totalW = bounds.size.width;

    // 4 Telemetry Tiles
    CGFloat tileW = (totalW - 3 * 8.0) / 4.0;
    CGFloat tileH = 50.0;
    CGFloat tileY = 28.0;

    NSString *val0 = [NSString stringWithFormat:@"%.1f kHz", (_sampleRate > 0 ? _sampleRate : 44100) / 1000.0];
    NSString *sub0 = @"Sample Rate";

    float msBuf = (_bufferSize > 0 && _sampleRate > 0) ? ((float)_bufferSize / (float)_sampleRate * 1000.0f) : 11.6f;
    NSString *val1 = [NSString stringWithFormat:@"%u smp (%.1f ms)", _bufferSize > 0 ? _bufferSize : 512, msBuf];
    NSString *sub1 = @"Active CoreAudio Buffer";

    NSString *val2 = [NSString stringWithFormat:@"~%.1f ms RTL", _estRtlMs > 0 ? _estRtlMs : 24.8f];
    NSString *sub2 = @"Est. Round-Trip Latency";
    NSColor *colRtl = (_estRtlMs <= 7.0f) ? ColorHex(0x00e676, 1.0) : ((_estRtlMs <= 15.0f) ? ColorHex(0x38bdf8, 1.0) : ColorHex(0xfbbf24, 1.0));

    NSString *val3 = _isOnline ? @"● ONLINE" : @"● OFFLINE";
    NSString *sub3 = _isOnline ? @"Hardware Clock Locked" : @"Device Disconnected";
    NSColor *colSt = _isOnline ? ColorHex(0x34d399, 1.0) : ColorHex(0xf87171, 1.0);

    NSArray *tiles = @[
        @{@"val": val0, @"sub": sub0, @"col": ColorHex(0x38bdf8, 1.0)},
        @{@"val": val1, @"sub": sub1, @"col": ColorHex(0x38bdf8, 1.0)},
        @{@"val": val2, @"sub": sub2, @"col": colRtl},
        @{@"val": val3, @"sub": sub3, @"col": colSt}
    ];

    NSDictionary *subAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica" size:8.5] ?: [NSFont systemFontOfSize:8.5],
        NSForegroundColorAttributeName: ColorHex(0x94a3b8, 1.0)
    };

    for (int i = 0; i < 4; i++) {
        CGFloat x = i * (tileW + 8.0);
        NSRect tRect = NSMakeRect(x, tileY, tileW, tileH);
        
        [ColorHex(0x181f2c, 1.0) setFill];
        [[NSBezierPath bezierPathWithRoundedRect:tRect xRadius:5 yRadius:5] fill];
        [ColorHex(0x232d40, 1.0) setStroke];
        [[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(tRect, 0.5, 0.5) xRadius:5 yRadius:5] stroke];

        NSDictionary *valAttr = @{
            NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:11.5] ?: [NSFont boldSystemFontOfSize:11.5],
            NSForegroundColorAttributeName: tiles[i][@"col"]
        };
        NSSize vSize = [tiles[i][@"val"] sizeWithAttributes:valAttr];
        [tiles[i][@"val"] drawAtPoint:NSMakePoint(x + (tileW - vSize.width)/2.0, tRect.origin.y + 26) withAttributes:valAttr];

        NSSize sSize = [tiles[i][@"sub"] sizeWithAttributes:subAttr];
        [tiles[i][@"sub"] drawAtPoint:NSMakePoint(x + (tileW - sSize.width)/2.0, tRect.origin.y + 8) withAttributes:subAttr];
    }

    // Bottom Confirmation Banner
    NSRect bRect = NSMakeRect(0, 0, totalW, 22);
    [ColorHex(0x131a26, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:bRect xRadius:4 yRadius:4] fill];
    [ColorHex(0x1d293d, 1.0) setStroke];
    [[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(bRect, 0.5, 0.5) xRadius:4 yRadius:4] stroke];

    NSString *tag = _isConfirmed ? @"CoreAudio Confirmed" : @"Synced";
    NSString *banner = [NSString stringWithFormat:@"✓ %@: Buffer = %u smp (%.2f ms) | Round-Trip Latency: ~%.1f ms RTL | Clock: %.1f kHz Locked",
                        tag, _bufferSize > 0 ? _bufferSize : 512, msBuf, _estRtlMs > 0 ? _estRtlMs : 24.8f, (_sampleRate > 0 ? _sampleRate : 44100) / 1000.0];
    NSDictionary *bAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.5] ?: [NSFont boldSystemFontOfSize:8.5],
        NSForegroundColorAttributeName: ColorHex(0x38bdf8, 1.0)
    };
    NSSize bSize = [banner sizeWithAttributes:bAttr];
    [banner drawAtPoint:NSMakePoint((totalW - bSize.width)/2.0, 5) withAttributes:bAttr];
}
@end

// 3. Latency Engine Profile & Buffer Control Bar
@interface ControlBarView : NSView
@property (nonatomic, assign) int activeMode;
@property (nonatomic, assign) UInt32 activeBuffer;
@property (nonatomic, copy) void (^onModeSelect)(int mode);
@property (nonatomic, copy) void (^onBufferSelect)(UInt32 buf);
@end

@implementation ControlBarView
- (void)drawRect:(NSRect)dirtyRect {
    NSRect bounds = self.bounds;
    [ColorHex(0x151922, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:bounds xRadius:6 yRadius:6] fill];
    [ColorHex(0x232a3b, 1.0) setStroke];
    [[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(bounds, 0.5, 0.5) xRadius:6 yRadius:6] stroke];

    // Left Title: LATENCY PROFILE
    NSDictionary *hdrAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.0] ?: [NSFont boldSystemFontOfSize:8.0],
        NSForegroundColorAttributeName: ColorHex(0x94a3b8, 1.0)
    };
    [@"LATENCY ENGINE PROFILE" drawAtPoint:NSMakePoint(14, bounds.size.height - 18) withAttributes:hdrAttr];

    // 3 Profile Buttons
    NSArray *profiles = @[@"⚡ Ultra-Low (Live)", @"✓ Balanced (Studio)", @"🛡 Safe (Heavy Mix)"];
    CGFloat profStartX = 14.0;
    CGFloat profW = 120.0;
    for (int i = 0; i < 3; i++) {
        NSRect pr = NSMakeRect(profStartX + i * (profW + 6.0), 10, profW, 26);
        BOOL isActive = (i == _activeMode);
        
        NSColor *bgCol = isActive ? (i == 0 ? ColorHex(0x065f46, 1.0) : (i == 1 ? ColorHex(0x1e40af, 1.0) : ColorHex(0x92400e, 1.0))) : ColorHex(0x1e2434, 1.0);
        NSColor *fgCol = isActive ? [NSColor whiteColor] : ColorHex(0x94a3b8, 1.0);

        [bgCol setFill];
        [[NSBezierPath bezierPathWithRoundedRect:pr xRadius:4 yRadius:4] fill];
        if (isActive) {
            [ColorHex(0x38bdf8, 1.0) setStroke];
            [[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(pr, 0.5, 0.5) xRadius:4 yRadius:4] stroke];
        }

        NSDictionary *pAttr = @{
            NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.5] ?: [NSFont boldSystemFontOfSize:8.5],
            NSForegroundColorAttributeName: fgCol
        };
        NSSize s = [profiles[i] sizeWithAttributes:pAttr];
        [profiles[i] drawAtPoint:NSMakePoint(pr.origin.x + (profW - s.width)/2.0, pr.origin.y + 7.5) withAttributes:pAttr];
    }

    // Right Title: BUFFER SIZE (COREAUDIO SAMPLES)
    CGFloat bufStartX = 410.0;
    [@"BUFFER SIZE (COREAUDIO SAMPLES)" drawAtPoint:NSMakePoint(bufStartX, bounds.size.height - 18) withAttributes:hdrAttr];

    NSDictionary *syncAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica" size:7.5] ?: [NSFont systemFontOfSize:7.5],
        NSForegroundColorAttributeName: ColorHex(0x64748b, 1.0)
    };
    NSString *syncStr = @"Synchronized with Studio One / Logic / Live";
    NSSize syncSize = [syncStr sizeWithAttributes:syncAttr];
    [syncStr drawAtPoint:NSMakePoint(bounds.size.width - 14 - syncSize.width, bounds.size.height - 18) withAttributes:syncAttr];

    // 8 Buffer Buttons
    CGFloat totalBufAreaW = bounds.size.width - 14 - bufStartX;
    CGFloat bufBtnW = (totalBufAreaW - (NUM_BUF_SIZES - 1) * 4.0) / (CGFloat)NUM_BUF_SIZES;

    for (int i = 0; i < NUM_BUF_SIZES; i++) {
        UInt32 val = g_bufSizes[i];
        NSRect br = NSMakeRect(bufStartX + i * (bufBtnW + 4.0), 10, bufBtnW, 26);
        BOOL isCur = (val == _activeBuffer);

        NSColor *bg = isCur ? ColorHex(0x0284c7, 1.0) : ColorHex(0x1e2434, 1.0);
        NSColor *fg = isCur ? [NSColor whiteColor] : ColorHex(0x94a3b8, 1.0);

        [bg setFill];
        [[NSBezierPath bezierPathWithRoundedRect:br xRadius:4 yRadius:4] fill];
        if (isCur) {
            [ColorHex(0x38bdf8, 1.0) setStroke];
            [[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(br, 0.5, 0.5) xRadius:4 yRadius:4] stroke];
        }

        NSString *label = isCur ? [NSString stringWithFormat:@"✓ %u", val] : [NSString stringWithFormat:@"%u", val];
        NSDictionary *bAttr = @{
            NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.5] ?: [NSFont boldSystemFontOfSize:8.5],
            NSForegroundColorAttributeName: fg
        };
        NSSize ls = [label sizeWithAttributes:bAttr];
        [label drawAtPoint:NSMakePoint(br.origin.x + (bufBtnW - ls.width)/2.0, br.origin.y + 7.5) withAttributes:bAttr];
    }
}

- (BOOL)acceptsFirstMouse:(NSEvent *)event {
    return YES;
}

- (void)resetCursorRects {
    [super resetCursorRects];
    NSRect bounds = self.bounds;
    CGFloat profStartX = 14.0;
    CGFloat profW = 120.0;
    for (int i = 0; i < 3; i++) {
        NSRect pr = NSMakeRect(profStartX + i * (profW + 6.0), 0, profW, bounds.size.height - 15);
        [self addCursorRect:pr cursor:[NSCursor pointingHandCursor]];
    }
    CGFloat bufStartX = 410.0;
    CGFloat totalBufAreaW = bounds.size.width - 14 - bufStartX;
    CGFloat bufBtnW = (totalBufAreaW - (NUM_BUF_SIZES - 1) * 4.0) / (CGFloat)NUM_BUF_SIZES;
    for (int i = 0; i < NUM_BUF_SIZES; i++) {
        NSRect br = NSMakeRect(bufStartX + i * (bufBtnW + 4.0), 0, bufBtnW, bounds.size.height - 15);
        [self addCursorRect:br cursor:[NSCursor pointingHandCursor]];
    }
}

- (void)mouseDown:(NSEvent *)event {
    NSPoint p = [self convertPoint:[event locationInWindow] fromView:nil];
    NSRect bounds = self.bounds;
    
    // Check Profiles (hit area covers entire button box)
    CGFloat profStartX = 14.0;
    CGFloat profW = 120.0;
    for (int i = 0; i < 3; i++) {
        NSRect pr = NSMakeRect(profStartX + i * (profW + 6.0), 0, profW, bounds.size.height - 15);
        if (NSPointInRect(p, pr)) {
            _activeMode = i;
            if (_onModeSelect) _onModeSelect(i);
            [self setNeedsDisplay:YES];
            return;
        }
    }

    // Check Buffers (hit area covers entire button box)
    CGFloat bufStartX = 410.0;
    CGFloat totalBufAreaW = bounds.size.width - 14 - bufStartX;
    CGFloat bufBtnW = (totalBufAreaW - (NUM_BUF_SIZES - 1) * 4.0) / (CGFloat)NUM_BUF_SIZES;

    for (int i = 0; i < NUM_BUF_SIZES; i++) {
        NSRect br = NSMakeRect(bufStartX + i * (bufBtnW + 4.0), 0, bufBtnW, bounds.size.height - 15);
        if (NSPointInRect(p, br)) {
            _activeBuffer = g_bufSizes[i];
            if (_onBufferSelect) _onBufferSelect(_activeBuffer);
            [self setNeedsDisplay:YES];
            return;
        }
    }
}
@end

// 4. 16-Channel Hardware Input Meter Bridge View
@interface MeterBridgeView : NSView {
@public
    float inVals[16];
    float inDbs[16];
    float inHolds[16];
    NSTimeInterval inHoldTimes[16];
    NSTimeInterval inClipTimes[16];
    BOOL inMutes[16];
    BOOL inSolos[16];
}
@end

@implementation MeterBridgeView
- (BOOL)acceptsFirstMouse:(NSEvent *)event {
    return YES;
}

- (instancetype)initWithFrame:(NSRect)frameRect {
    self = [super initWithFrame:frameRect];
    if (self) {
        InitStaticColors();
        for (int i = 0; i < 16; i++) {
            inVals[i] = 0.0f;
            inDbs[i] = -60.0f;
            inHolds[i] = 0.0f;
            inHoldTimes[i] = 0.0;
            inClipTimes[i] = 0.0;
            inMutes[i] = NO;
            inSolos[i] = NO;
        }
    }
    return self;
}

- (void)drawRect:(NSRect)dirtyRect {
    InitStaticColors();
    NSRect bounds = self.bounds;
    NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];

    // Card background
    [ColorHex(0x151922, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:bounds xRadius:6 yRadius:6] fill];
    [ColorHex(0x232a3b, 1.0) setStroke];
    [[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(bounds, 0.5, 0.5) xRadius:6 yRadius:6] stroke];

    // Top Group Header Bar
    NSDictionary *ghPreamps = @{NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.0] ?: [NSFont boldSystemFontOfSize:8.0], NSForegroundColorAttributeName: ColorHex(0x38bdf8, 1.0)};
    NSDictionary *ghGuitar = @{NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.0] ?: [NSFont boldSystemFontOfSize:8.0], NSForegroundColorAttributeName: ColorHex(0xfbbf24, 1.0)};
    NSDictionary *ghLine = @{NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.0] ?: [NSFont boldSystemFontOfSize:8.0], NSForegroundColorAttributeName: ColorHex(0xc084fc, 1.0)};
    NSDictionary *ghDigital = @{NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.0] ?: [NSFont boldSystemFontOfSize:8.0], NSForegroundColorAttributeName: ColorHex(0xf472b6, 1.0)};

    [@"PREAMPS 1-8 (MIC/LINE XLR)" drawAtPoint:NSMakePoint(14, bounds.size.height - 18) withAttributes:ghPreamps];
    [@"GUITAR 9-10" drawAtPoint:NSMakePoint(388, bounds.size.height - 18) withAttributes:ghGuitar];
    [@"LINE 11-14" drawAtPoint:NSMakePoint(490, bounds.size.height - 18) withAttributes:ghLine];
    [@"DIGITAL 15-16" drawAtPoint:NSMakePoint(676, bounds.size.height - 18) withAttributes:ghDigital];

    CGFloat totalH = bounds.size.height;
    CGFloat startX = 10.0;
    CGFloat stripW = 43.0;
    CGFloat stripGap = 2.5;

    NSDictionary *tagAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica" size:7.0] ?: [NSFont systemFontOfSize:7.0],
        NSForegroundColorAttributeName: ColorHex(0x64748b, 1.0)
    };
    NSDictionary *dbAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Menlo-Bold" size:7.5] ?: [NSFont boldSystemFontOfSize:7.5],
        NSForegroundColorAttributeName: ColorHex(0x94a3b8, 1.0)
    };

    for (int i = 0; i < 16; i++) {
        CGFloat x = startX + i * (stripW + stripGap);
        if (i >= 8) x += 4.0;
        if (i >= 10) x += 4.0;
        if (i >= 14) x += 4.0;

        NSRect stripRect = NSMakeRect(x, 10, stripW, totalH - 34);

        // Strip bezel frame
        [ColorHex(0x10131a, 1.0) setFill];
        NSBezierPath *sp = [NSBezierPath bezierPathWithRoundedRect:stripRect xRadius:4 yRadius:4];
        [sp fill];
        [ColorHex(0x1e2434, 1.0) setStroke];
        [sp stroke];

        // 1. Channel Title Badge
        NSString *name;
        NSString *tag;
        NSColor *badgeCol;
        if (i < 8) {
            name = [NSString stringWithFormat:@"MIC %d", i + 1];
            tag = @"XLR";
            badgeCol = ColorHex(0x38bdf8, 1.0);
        } else if (i < 10) {
            name = [NSString stringWithFormat:@"GTR %d", i + 1];
            tag = @"INST";
            badgeCol = ColorHex(0xfbbf24, 1.0);
        } else if (i < 14) {
            name = [NSString stringWithFormat:@"LINE %d", i + 1];
            tag = @"LINE";
            badgeCol = ColorHex(0xc084fc, 1.0);
        } else {
            name = [NSString stringWithFormat:@"SPD %d", i + 1];
            tag = @"COAX";
            badgeCol = ColorHex(0xf472b6, 1.0);
        }

        NSRect badgeRect = NSMakeRect(x + 2, stripRect.origin.y + stripRect.size.height - 24, stripW - 4, 20);
        [ColorHex(0x181f2c, 1.0) setFill];
        [[NSBezierPath bezierPathWithRoundedRect:badgeRect xRadius:3 yRadius:3] fill];

        NSDictionary *nameAttr = @{
            NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:7.5] ?: [NSFont boldSystemFontOfSize:7.5],
            NSForegroundColorAttributeName: badgeCol
        };
        NSSize nameSize = [name sizeWithAttributes:nameAttr];
        [name drawAtPoint:NSMakePoint(x + (stripW - nameSize.width)/2.0, badgeRect.origin.y + 9.5) withAttributes:nameAttr];

        NSSize tagSize = [tag sizeWithAttributes:tagAttr];
        [tag drawAtPoint:NSMakePoint(x + (stripW - tagSize.width)/2.0, badgeRect.origin.y + 1.5) withAttributes:tagAttr];

        // 2. Overload Clip LED
        BOOL isClip = (now - inClipTimes[i] < 1.8);
        NSRect clipRect = NSMakeRect(x + 10, badgeRect.origin.y - 10, stripW - 20, 7);
        if (isClip) {
            [g_colLedRed setFill];
            [[NSBezierPath bezierPathWithRoundedRect:clipRect xRadius:2 yRadius:2] fill];
        } else {
            [g_colClipOffFill setFill];
            [[NSBezierPath bezierPathWithRoundedRect:clipRect xRadius:2 yRadius:2] fill];
            [g_colClipOffStroke setStroke];
            [[NSBezierPath bezierPathWithRoundedRect:clipRect xRadius:2 yRadius:2] stroke];
        }

        // 3. 26 Hardware Segmented LEDs
        CGFloat meterBottom = stripRect.origin.y + 24;
        CGFloat segW = stripW - 16;
        CGFloat segH = 4.5;
        CGFloat segGap = 1.5;
        int activeCount = (int)(inVals[i] * 26.0f);
        if (activeCount > 26) activeCount = 26;

        for (int s = 0; s < 26; s++) {
            CGFloat segY = meterBottom + s * (segH + segGap);
            NSRect segRect = NSMakeRect(x + 8, segY, segW, segH);

            if (s < activeCount) {
                NSColor *onCol;
                if (s >= 23)      onCol = g_colLedRed;
                else if (s >= 19) onCol = g_colLedOrange;
                else if (s >= 13) onCol = g_colLedYellow;
                else if (s >= 5)  onCol = g_colLedGreen1;
                else              onCol = g_colLedGreen2;
                [onCol setFill];
                NSRectFill(segRect);
            } else {
                [g_colLedOffFill setFill];
                NSRectFill(segRect);
                [g_colLedOffStroke setStroke];
                NSFrameRect(segRect);
            }
        }

        // 4. Peak Hold Bar
        if (inHolds[i] > 0.04f) {
            int holdIdx = (int)(inHolds[i] * 25.0f);
            if (holdIdx > 25) holdIdx = 25;
            CGFloat holdY = meterBottom + holdIdx * (segH + segGap);
            NSRect holdRect = NSMakeRect(x + 8, holdY, segW, 1.5);
            [g_colWhite setFill];
            NSRectFill(holdRect);
        }

        // 5. Monospace dBFS readout at strip base
        float curDb = inDbs[i];
        NSString *dbStr;
        if (isClip) {
            dbStr = @"CLIP";
        } else if (inVals[i] < 0.01f || curDb <= -58.0f) {
            dbStr = @"-inf";
        } else {
            dbStr = [NSString stringWithFormat:@"%.1f", curDb];
        }
        NSSize dbSize = [dbStr sizeWithAttributes:dbAttr];
        [dbStr drawAtPoint:NSMakePoint(x + (stripW - dbSize.width)/2.0, stripRect.origin.y + 6) withAttributes:dbAttr];
    }
}

- (void)mouseDown:(NSEvent *)event {
    (void)event;
}
@end

// 5. Master Monitor Console View
@interface MasterMonitorView : NSView {
@public
    float outVals[2];
    float outDbs[2];
    float outHolds[2];
    NSTimeInterval outHoldTimes[2];
    NSTimeInterval outClipTimes[2];
}
@end

@implementation MasterMonitorView
- (BOOL)acceptsFirstMouse:(NSEvent *)event {
    return NO;
}

- (instancetype)initWithFrame:(NSRect)frameRect {
    self = [super initWithFrame:frameRect];
    if (self) {
        InitStaticColors();
        outVals[0] = outVals[1] = 0.0f;
        outDbs[0] = outDbs[1] = -60.0f;
        outHolds[0] = outHolds[1] = 0.0f;
        outHoldTimes[0] = outHoldTimes[1] = 0.0;
        outClipTimes[0] = outClipTimes[1] = 0.0;
    }
    return self;
}

- (void)drawRect:(NSRect)dirtyRect {
    InitStaticColors();
    NSRect bounds = self.bounds;
    NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];

    // Card background
    [ColorHex(0x151922, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:bounds xRadius:6 yRadius:6] fill];
    [ColorHex(0x232a3b, 1.0) setStroke];
    [[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(bounds, 0.5, 0.5) xRadius:6 yRadius:6] stroke];

    // Header
    NSDictionary *hAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:9.5] ?: [NSFont boldSystemFontOfSize:9.5],
        NSForegroundColorAttributeName: [NSColor whiteColor]
    };
    [@"MASTER MONITOR" drawAtPoint:NSMakePoint(12, bounds.size.height - 20) withAttributes:hAttr];

    NSDictionary *subAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica" size:7.5] ?: [NSFont systemFontOfSize:7.5],
        NSForegroundColorAttributeName: ColorHex(0x94a3b8, 1.0)
    };
    [@"Out 1-2 (Main) • Out 3-4 (Cue)" drawAtPoint:NSMakePoint(12, bounds.size.height - 32) withAttributes:subAttr];

    // Stereo Master Peak Meters
    CGFloat meterBottom = 26.0;
    CGFloat segW = 22.0;
    CGFloat segH = 4.8;
    CGFloat segGap = 1.6;
    CGFloat leftX = 22.0;
    CGFloat rightX = bounds.size.width - 22.0 - segW;

    // "L" and "R" labels
    NSDictionary *lrAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.0] ?: [NSFont boldSystemFontOfSize:8.0],
        NSForegroundColorAttributeName: ColorHex(0x38bdf8, 1.0)
    };
    [@"L" drawAtPoint:NSMakePoint(leftX + 8, bounds.size.height - 48) withAttributes:lrAttr];
    [@"R" drawAtPoint:NSMakePoint(rightX + 8, bounds.size.height - 48) withAttributes:lrAttr];

    // Clip LEDs
    BOOL clipL = (now - outClipTimes[0] < 1.8);
    BOOL clipR = (now - outClipTimes[1] < 1.8);

    NSRect clipRectL = NSMakeRect(leftX + 2, bounds.size.height - 58, segW - 4, 6);
    NSRect clipRectR = NSMakeRect(rightX + 2, bounds.size.height - 58, segW - 4, 6);

    [clipL ? g_colLedRed : g_colClipOffFill setFill];
    [[NSBezierPath bezierPathWithRoundedRect:clipRectL xRadius:2 yRadius:2] fill];
    [clipR ? g_colLedRed : g_colClipOffFill setFill];
    [[NSBezierPath bezierPathWithRoundedRect:clipRectR xRadius:2 yRadius:2] fill];

    // 26 Segments for L and R
    int actL = (int)(outVals[0] * 26.0f);
    int actR = (int)(outVals[1] * 26.0f);

    for (int s = 0; s < 26; s++) {
        CGFloat segY = meterBottom + s * (segH + segGap);
        NSRect rL = NSMakeRect(leftX, segY, segW, segH);
        NSRect rR = NSMakeRect(rightX, segY, segW, segH);

        NSColor *onCol;
        if (s >= 23)      onCol = g_colLedRed;
        else if (s >= 19) onCol = g_colLedOrange;
        else if (s >= 13) onCol = g_colLedYellow;
        else if (s >= 5)  onCol = g_colLedGreen1;
        else              onCol = g_colLedGreen2;

        // Left
        if (s < actL) {
            [onCol setFill];
            NSRectFill(rL);
        } else {
            [g_colLedOffFill setFill];
            NSRectFill(rL);
            [g_colLedOffStroke setStroke];
            NSFrameRect(rL);
        }

        // Right
        if (s < actR) {
            [onCol setFill];
            NSRectFill(rR);
        } else {
            [g_colLedOffFill setFill];
            NSRectFill(rR);
            [g_colLedOffStroke setStroke];
            NSFrameRect(rR);
        }
    }

    // Center dB Scale Ticks
    CGFloat centerX = (leftX + segW + rightX) / 2.0;
    NSArray *ticks = @[
        @{@"db": @0, @"text": @"0 dB", @"col": ColorHex(0xff1744, 1.0)},
        @{@"db": @3, @"text": @"-3", @"col": ColorHex(0xff9100, 1.0)},
        @{@"db": @6, @"text": @"-6", @"col": ColorHex(0xffd600, 1.0)},
        @{@"db": @12, @"text": @"-12", @"col": ColorHex(0x00e676, 1.0)},
        @{@"db": @18, @"text": @"-18", @"col": ColorHex(0x00e676, 1.0)},
        @{@"db": @24, @"text": @"-24", @"col": ColorHex(0x00c853, 1.0)},
        @{@"db": @36, @"text": @"-36", @"col": ColorHex(0x64748b, 1.0)},
        @{@"db": @48, @"text": @"-48", @"col": ColorHex(0x64748b, 1.0)}
    ];

    for (NSDictionary *t in ticks) {
        float db = -[t[@"db"] floatValue];
        float norm = DbToNorm(db);
        int idx = (int)(norm * 25.0f);
        CGFloat y = meterBottom + idx * (segH + segGap);

        NSDictionary *tAttr = @{
            NSFontAttributeName: [NSFont fontWithName:@"Menlo-Bold" size:6.5] ?: [NSFont boldSystemFontOfSize:6.5],
            NSForegroundColorAttributeName: t[@"col"]
        };
        NSSize sz = [t[@"text"] sizeWithAttributes:tAttr];
        [t[@"text"] drawAtPoint:NSMakePoint(centerX - sz.width/2.0, y - 2) withAttributes:tAttr];
    }

    // Monospace Readout at bottom
    NSDictionary *dbAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Menlo-Bold" size:7.0] ?: [NSFont boldSystemFontOfSize:7.0],
        NSForegroundColorAttributeName: ColorHex(0x94a3b8, 1.0)
    };
    NSString *dbLStr = clipL ? @"CLIP" : (outVals[0] < 0.01f || outDbs[0] <= -58.0f ? @"-inf" : [NSString stringWithFormat:@"%.1f", outDbs[0]]);
    NSString *dbRStr = clipR ? @"CLIP" : (outVals[1] < 0.01f || outDbs[1] <= -58.0f ? @"-inf" : [NSString stringWithFormat:@"%.1f", outDbs[1]]);
    NSSize sL = [dbLStr sizeWithAttributes:dbAttr];
    NSSize sR = [dbRStr sizeWithAttributes:dbAttr];
    [dbLStr drawAtPoint:NSMakePoint(leftX + (segW - sL.width)/2.0, 8) withAttributes:dbAttr];
    [dbRStr drawAtPoint:NSMakePoint(rightX + (segW - sR.width)/2.0, 8) withAttributes:dbAttr];
}
@end

// 6. Studio Utility Bar View
@interface UtilityBarView : NSView
@property (nonatomic, copy) void (^onToneTest)(void);
@property (nonatomic, copy) void (^onRecordToggle)(void);
@property (nonatomic, copy) void (^onOpenFolder)(void);
@property (nonatomic, copy) void (^onMidiSetup)(void);
@property (nonatomic, copy) void (^onSoundPrefs)(void);
@property (nonatomic, copy) void (^onRestartEngine)(void);
@property (nonatomic, assign) BOOL isRecording;
@property (nonatomic, assign) NSTimeInterval recStartTime;
@end

@implementation UtilityBarView
- (BOOL)acceptsFirstMouse:(NSEvent *)event {
    return YES;
}
- (void)drawRect:(NSRect)dirtyRect {
    NSRect bounds = self.bounds;
    [ColorHex(0x151922, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:bounds xRadius:6 yRadius:6] fill];
    [ColorHex(0x232a3b, 1.0) setStroke];
    [[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(bounds, 0.5, 0.5) xRadius:6 yRadius:6] stroke];

    CGFloat btnH = 24.0;
    CGFloat btnY = (bounds.size.height - btnH)/2.0;

    NSDictionary *btnAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.5] ?: [NSFont boldSystemFontOfSize:8.5],
        NSForegroundColorAttributeName: [NSColor whiteColor]
    };
    NSDictionary *secAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica" size:8.5] ?: [NSFont systemFontOfSize:8.5],
        NSForegroundColorAttributeName: ColorHex(0x94a3b8, 1.0)
    };

    // 1. Tone Button
    NSRect toneRect = NSMakeRect(12, btnY, 150, btnH);
    [ColorHex(0x1e2434, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:toneRect xRadius:4 yRadius:4] fill];
    NSDictionary *toneAttr = @{
        NSFontAttributeName: [NSFont fontWithName:@"Helvetica-Bold" size:8.5] ?: [NSFont boldSystemFontOfSize:8.5],
        NSForegroundColorAttributeName: ColorHex(0x38bdf8, 1.0)
    };
    NSString *toneStr = @"♫ Reference Tone (440 Hz)";
    NSSize ts = [toneStr sizeWithAttributes:toneAttr];
    [toneStr drawAtPoint:NSMakePoint(toneRect.origin.x + (150 - ts.width)/2.0, toneRect.origin.y + 6) withAttributes:toneAttr];

    // 2. Record Button
    NSString *recStr = _isRecording ? @"■ STOP RECORDING" : @"● Record 16 Tracks (WAV)";
    NSColor *recBg = _isRecording ? ColorHex(0x991b1b, 1.0) : ColorHex(0xdc2626, 1.0);
    NSRect recRect = NSMakeRect(168, btnY, 155, btnH);
    [recBg setFill];
    [[NSBezierPath bezierPathWithRoundedRect:recRect xRadius:4 yRadius:4] fill];
    NSSize rs = [recStr sizeWithAttributes:btnAttr];
    [recStr drawAtPoint:NSMakePoint(recRect.origin.x + (155 - rs.width)/2.0, recRect.origin.y + 6) withAttributes:btnAttr];

    // 3. Folder Button
    NSRect foldRect = NSMakeRect(329, btnY, 70, btnH);
    [ColorHex(0x1e2434, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:foldRect xRadius:4 yRadius:4] fill];
    NSString *foldStr = @"📁 Folder";
    NSSize fs = [foldStr sizeWithAttributes:secAttr];
    [foldStr drawAtPoint:NSMakePoint(foldRect.origin.x + (70 - fs.width)/2.0, foldRect.origin.y + 6) withAttributes:secAttr];

    // Right Utilities: Restart, Sound, MIDI
    CGFloat curX = bounds.size.width - 12;

    // Restart Engine
    NSRect restRect = NSMakeRect(curX - 120, btnY, 120, btnH);
    [ColorHex(0x2563eb, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:restRect xRadius:4 yRadius:4] fill];
    NSString *restStr = @"🔄 Restart Engine";
    NSSize restS = [restStr sizeWithAttributes:btnAttr];
    [restStr drawAtPoint:NSMakePoint(restRect.origin.x + (120 - restS.width)/2.0, restRect.origin.y + 6) withAttributes:btnAttr];
    curX -= 126;

    // Sound Settings
    NSRect sndRect = NSMakeRect(curX - 105, btnY, 105, btnH);
    [ColorHex(0x1e2434, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:sndRect xRadius:4 yRadius:4] fill];
    NSString *sndStr = @"⚙ Sound Settings";
    NSSize sndS = [sndStr sizeWithAttributes:secAttr];
    [sndStr drawAtPoint:NSMakePoint(sndRect.origin.x + (105 - sndS.width)/2.0, sndRect.origin.y + 6) withAttributes:secAttr];
    curX -= 111;

    // Audio MIDI Setup
    NSRect midiRect = NSMakeRect(curX - 125, btnY, 125, btnH);
    [ColorHex(0x1e2434, 1.0) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:midiRect xRadius:4 yRadius:4] fill];
    NSString *midiStr = @"🎹 Audio MIDI Setup";
    NSSize midiS = [midiStr sizeWithAttributes:secAttr];
    [midiStr drawAtPoint:NSMakePoint(midiRect.origin.x + (125 - midiS.width)/2.0, midiRect.origin.y + 6) withAttributes:secAttr];
}

- (void)mouseDown:(NSEvent *)event {
    NSPoint p = [self convertPoint:[event locationInWindow] fromView:nil];
    NSRect bounds = self.bounds;
    CGFloat btnH = 24.0;
    CGFloat btnY = (bounds.size.height - btnH)/2.0;

    // Tone
    if (NSPointInRect(p, NSMakeRect(12, btnY, 150, btnH))) {
        if (_onToneTest) _onToneTest();
        return;
    }
    // Record
    if (NSPointInRect(p, NSMakeRect(168, btnY, 155, btnH))) {
        if (_onRecordToggle) _onRecordToggle();
        return;
    }
    // Folder
    if (NSPointInRect(p, NSMakeRect(329, btnY, 70, btnH))) {
        if (_onOpenFolder) _onOpenFolder();
        return;
    }

    CGFloat curX = bounds.size.width - 12;
    // Restart
    if (NSPointInRect(p, NSMakeRect(curX - 120, btnY, 120, btnH))) {
        if (_onRestartEngine) _onRestartEngine();
        return;
    }
    curX -= 126;
    // Sound
    if (NSPointInRect(p, NSMakeRect(curX - 105, btnY, 105, btnH))) {
        if (_onSoundPrefs) _onSoundPrefs();
        return;
    }
    curX -= 111;
    // MIDI
    if (NSPointInRect(p, NSMakeRect(curX - 125, btnY, 125, btnH))) {
        if (_onMidiSetup) _onMidiSetup();
        return;
    }
}
@end

#pragma mark - Main App Controller

@interface TascamConsoleController : NSObject <NSApplicationDelegate, NSWindowDelegate>
@property (nonatomic, strong) NSWindow *window;
@property (nonatomic, strong) HeaderView *headerView;
@property (nonatomic, strong) TelemetryDeckView *telemetryView;
@property (nonatomic, strong) ControlBarView *controlBarView;
@property (nonatomic, strong) MeterBridgeView *meterBridgeView;
@property (nonatomic, strong) MasterMonitorView *masterView;
@property (nonatomic, strong) UtilityBarView *utilityBarView;

@property (nonatomic, assign) AudioDeviceID devId;
@property (nonatomic, assign) TascamSharedBuffer *shm;
@property (nonatomic, strong) NSTimer *timer;
@property (nonatomic, assign) UInt32 currentRate;
@property (nonatomic, assign) UInt32 currentBuffer;
@property (nonatomic, assign) UInt32 userRequestedBuffer;
@property (nonatomic, assign) NSTimeInterval userRequestTime;
@property (nonatomic, assign) int currentMode;
@property (nonatomic, assign) uint64_t lastHeartbeat;
@property (nonatomic, assign) NSTimeInterval lastHeartbeatTime;
@end

@implementation TascamConsoleController

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    InitStaticColors();
    _devId = FindTascamDeviceID();
    _currentRate = 44100;
    _currentBuffer = 128;
    _currentMode = TASCAM_MODE_LOW_LATENCY;
    _userRequestedBuffer = 0;

    // Load persisted mode
    FILE *f = fopen("/tmp/tascam_mode.conf", "r");
    if (!f) f = fopen(TASCAM_CONF_PATH, "r");
    if (f) {
        int m = 0;
        if (fscanf(f, "%d", &m) == 1 && (m >= 0 && m <= 2)) {
            _currentMode = m;
        }
        fclose(f);
    }

    // Attach Shared Memory
    int fd = shm_open(TASCAM_SHM_NAME, O_RDWR, 0666);
    if (fd >= 0) {
        _shm = (TascamSharedBuffer *)mmap(NULL, sizeof(TascamSharedBuffer), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        close(fd);
        if (_shm && (_shm->magic != TASCAM_SHM_MAGIC || _shm->version != TASCAM_SHM_VERSION)) {
            munmap(_shm, sizeof(TascamSharedBuffer));
            _shm = NULL;
        }
    }

    // Direct CoreAudio query
    Float64 hwRate = GetHardwareSampleRate(_devId);
    if (hwRate > 0) _currentRate = (UInt32)hwRate;
    UInt32 hwBuf = GetHardwareBufferSize(_devId);
    if (hwBuf > 0) _currentBuffer = hwBuf;

    // Create Main Console Window
    NSRect frame = NSMakeRect(100, 100, WIN_W, WIN_H);
    _window = [[NSWindow alloc] initWithContentRect:frame
                                          styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable)
                                            backing:NSBackingStoreBuffered
                                              defer:NO];
    [_window setTitle:@"TASCAM US-1800  •  Pro Audio Control Console (Apple Silicon Native)"];
    [_window setBackgroundColor:ColorHex(0x0d1016, 1.0)];
    [_window setDelegate:self];
    [_window center];

    NSView *content = [_window contentView];

    // Build Views from bottom to top
    // 1. Bottom Utility Bar: y = 10, h = 38
    _utilityBarView = [[UtilityBarView alloc] initWithFrame:NSMakeRect(12, 10, WIN_W - 24, 38)];
    [content addSubview:_utilityBarView];

    // 2. Main Middle Section: y = 58, h = 310
    // Left: 16-Channel Hardware Input Meter Bridge
    _meterBridgeView = [[MeterBridgeView alloc] initWithFrame:NSMakeRect(12, 58, 765, 310)];
    [content addSubview:_meterBridgeView];

    // Right: Master Monitor Console
    _masterView = [[MasterMonitorView alloc] initWithFrame:NSMakeRect(785, 58, WIN_W - 24 - 773, 310)];
    [content addSubview:_masterView];

    // 3. Control Bar: y = 380, h = 65
    _controlBarView = [[ControlBarView alloc] initWithFrame:NSMakeRect(12, 380, WIN_W - 24, 65)];
    _controlBarView.activeMode = _currentMode;
    _controlBarView.activeBuffer = _currentBuffer;
    [content addSubview:_controlBarView];

    // 4. Telemetry Deck: y = 455, h = 90
    _telemetryView = [[TelemetryDeckView alloc] initWithFrame:NSMakeRect(12, 455, WIN_W - 24, 90)];
    _telemetryView.sampleRate = _currentRate;
    _telemetryView.bufferSize = _currentBuffer;
    _telemetryView.isOnline = (_devId != kAudioObjectUnknown);
    [content addSubview:_telemetryView];

    // 5. Header Rack: y = 555, h = 55
    _headerView = [[HeaderView alloc] initWithFrame:NSMakeRect(12, 555, WIN_W - 24, 55)];
    _headerView.isOnline = (_devId != kAudioObjectUnknown);
    [content addSubview:_headerView];

    // Callbacks & Actions
    __weak typeof(self) weakSelf = self;

    _controlBarView.onModeSelect = ^(int mode) {
        typeof(self) strongSelf = weakSelf;
        if (!strongSelf) return;
        strongSelf.currentMode = mode;
        if (strongSelf.shm) strongSelf.shm->latency_mode = mode;
        FILE *cf = fopen("/tmp/tascam_mode.conf", "w");
        if (cf) { fprintf(cf, "%d\n", mode); fclose(cf); }
        [strongSelf updateTelemetryDisplays:YES];
    };

    // Disable App Nap so switching Spaces / Virtual Desktops never throttles audio monitoring
    [[NSProcessInfo processInfo] beginActivityWithOptions:NSActivityUserInitiatedAllowingIdleSystemSleep reason:@"Pro Audio Monitoring"];

    _controlBarView.onBufferSelect = ^(UInt32 buf) {
        typeof(self) strongSelf = weakSelf;
        if (!strongSelf) return;
        strongSelf.currentBuffer = buf;
        strongSelf.userRequestedBuffer = buf;
        strongSelf.userRequestTime = [NSDate timeIntervalSinceReferenceDate];
        if (strongSelf.shm) strongSelf.shm->buffer_frame_size = buf;
        if (strongSelf.devId == kAudioObjectUnknown) {
            strongSelf.devId = FindTascamDeviceID();
        }
        BOOL ok = SetHardwareBufferSize(strongSelf.devId, buf);
        [strongSelf updateTelemetryDisplays:ok];
    };

    _utilityBarView.onToneTest = ^{
        typeof(self) strongSelf = weakSelf;
        if (!strongSelf) return;
        if (strongSelf.shm) {
            uint32_t wr = atomic_load_explicit(&strongSelf.shm->pb_wr, memory_order_relaxed);
            for (int i = 0; i < 22050; i++) { // 0.5s tone
                float sample = 0.5f * sinf(2.0f * M_PI * 440.0f * (float)i / 44100.0f);
                uint32_t slot = (wr + i) & TASCAM_RING_MASK;
                float *dst = &strongSelf.shm->pb_ring[slot * TASCAM_OUT_CHANNELS];
                dst[0] = sample;
                dst[1] = sample;
                dst[2] = 0.0f;
                dst[3] = 0.0f;
            }
            atomic_store_explicit(&strongSelf.shm->pb_wr, (wr + 22050) & TASCAM_RING_MASK, memory_order_release);
            strongSelf.shm->out_peak[0] = 0.5f;
            strongSelf.shm->out_peak[1] = 0.5f;
        }
        system("afplay /System/Library/Sounds/Ping.aiff &");
    };

    _utilityBarView.onOpenFolder = ^{
        NSString *path = [@"~/Music/TASCAM_Recordings" stringByExpandingTildeInPath];
        [[NSFileManager defaultManager] createDirectoryAtPath:path withIntermediateDirectories:YES attributes:nil error:nil];
        [[NSWorkspace sharedWorkspace] openURL:[NSURL fileURLWithPath:path]];
    };

    _utilityBarView.onMidiSetup = ^{
        [[NSWorkspace sharedWorkspace] openURL:[NSURL fileURLWithPath:@"/System/Applications/Utilities/Audio MIDI Setup.app"]];
    };

    _utilityBarView.onSoundPrefs = ^{
        [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:@"x-apple.systempreferences:com.apple.preference.sound"]];
    };

    _utilityBarView.onRestartEngine = ^{
        system("sudo launchctl kickstart -k system/com.tascam.us1800.live &");
    };

    if (_devId != kAudioObjectUnknown) {
        [self registerCoreAudioListeners];
    }

    [self updateTelemetryDisplays:YES];

    [_window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];

    // 30 FPS Smooth Real-Time Render & Telemetry Loop
    _timer = [NSTimer scheduledTimerWithTimeInterval:0.033 target:self selector:@selector(tick) userInfo:nil repeats:YES];
    [[NSRunLoop currentRunLoop] addTimer:_timer forMode:NSRunLoopCommonModes];
}

- (void)registerCoreAudioListeners {
    if (_devId == kAudioObjectUnknown) return;
    __weak typeof(self) weakSelf = self;
    AudioObjectPropertyAddress addrBuf = {
        kAudioDevicePropertyBufferFrameSize,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    AudioObjectAddPropertyListenerBlock(_devId, &addrBuf, dispatch_get_main_queue(), ^(UInt32 inNumberAddresses, const AudioObjectPropertyAddress *inAddresses) {
        typeof(self) strongSelf = weakSelf;
        if (!strongSelf || strongSelf.devId == kAudioObjectUnknown) return;
        UInt32 b = GetHardwareBufferSize(strongSelf.devId);
        if (b > 0 && b != strongSelf.currentBuffer) {
            strongSelf.currentBuffer = b;
            if (strongSelf.shm) strongSelf.shm->buffer_frame_size = b;
            [strongSelf updateTelemetryDisplays:YES];
        }
    });

    AudioObjectPropertyAddress addrRate = {
        kAudioDevicePropertyNominalSampleRate,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    AudioObjectAddPropertyListenerBlock(_devId, &addrRate, dispatch_get_main_queue(), ^(UInt32 inNumberAddresses, const AudioObjectPropertyAddress *inAddresses) {
        typeof(self) strongSelf = weakSelf;
        if (!strongSelf || strongSelf.devId == kAudioObjectUnknown) return;
        Float64 r = GetHardwareSampleRate(strongSelf.devId);
        if (r > 0 && (UInt32)r != strongSelf.currentRate) {
            strongSelf.currentRate = (UInt32)r;
            if (strongSelf.shm) strongSelf.shm->sample_rate = strongSelf.currentRate;
            [strongSelf updateTelemetryDisplays:YES];
        }
    });
}

- (void)updateTelemetryDisplays:(BOOL)confirmed {
    UInt32 rate = _currentRate > 0 ? _currentRate : 44100;
    UInt32 buf = _currentBuffer > 0 ? _currentBuffer : 512;

    float cushion = (_currentMode == TASCAM_MODE_LOW_LATENCY) ? 16.0f : ((_currentMode == TASCAM_MODE_BALANCED) ? 32.0f : 128.0f);
    float mult = (_currentMode == TASCAM_MODE_LOW_LATENCY) ? 2.0f : ((_currentMode == TASCAM_MODE_BALANCED) ? 3.0f : 4.0f);
    float fixed = (_currentMode == TASCAM_MODE_LOW_LATENCY) ? 1.2f : ((_currentMode == TASCAM_MODE_BALANCED) ? 2.0f : 4.0f);
    float rtl = (buf * mult / (float)rate * 1000.0f) + (cushion / (float)rate * 1000.0f) + fixed;

    _telemetryView.sampleRate = rate;
    _telemetryView.bufferSize = buf;
    _telemetryView.estRtlMs = rtl;
    _telemetryView.isConfirmed = confirmed;
    _telemetryView.isOnline = (_devId != kAudioObjectUnknown);

    _controlBarView.activeBuffer = buf;
    _controlBarView.activeMode = _currentMode;

    [_telemetryView setNeedsDisplay:YES];
    [_controlBarView setNeedsDisplay:YES];
    [_headerView setNeedsDisplay:YES];
}

- (void)tick {
    NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];

    // 1. CoreAudio Device Reconnect Check (handles daemon/coreaudiod restarts dynamically)
    if (_devId == kAudioObjectUnknown || GetHardwareBufferSize(_devId) == 0) {
        AudioDeviceID newDev = FindTascamDeviceID();
        if (newDev != kAudioObjectUnknown) {
            _devId = newDev;
            _headerView.isOnline = YES;
            _telemetryView.isOnline = YES;
            [self registerCoreAudioListeners];
            _currentBuffer = GetHardwareBufferSize(_devId);
            _currentRate = (UInt32)GetHardwareSampleRate(_devId);
            [self updateTelemetryDisplays:YES];
        } else {
            _devId = kAudioObjectUnknown;
            _headerView.isOnline = NO;
            _telemetryView.isOnline = NO;
            [self updateTelemetryDisplays:NO];
        }
    }

    // 2. Shared Memory Ballistics
    if (_shm) {
        uint64_t hb = _shm->engine_heartbeat;
        if (hb != _lastHeartbeat) {
            _lastHeartbeat = hb;
            _lastHeartbeatTime = now;
        }
        BOOL isStreaming = (now - _lastHeartbeatTime < 1.0) && (_shm->engine_running == 1);
        if (_headerView.isStreaming != isStreaming) {
            _headerView.isStreaming = isStreaming;
            [_headerView setNeedsDisplay:YES];
        }

        // Animate 16 Input Meters
        for (int i = 0; i < 16; i++) {
            float raw = _shm->in_peak[i];
            float db = PeakToDb(raw);
            float norm = DbToNorm(db);
            _meterBridgeView->inDbs[i] = db;

            if (norm > _meterBridgeView->inVals[i]) {
                _meterBridgeView->inVals[i] = norm;
            } else {
                _meterBridgeView->inVals[i] *= 0.86f;
                if (_meterBridgeView->inVals[i] < 0.002f) _meterBridgeView->inVals[i] = 0.0f;
            }

            if (norm >= _meterBridgeView->inHolds[i]) {
                _meterBridgeView->inHolds[i] = norm;
                _meterBridgeView->inHoldTimes[i] = now;
            } else if (now - _meterBridgeView->inHoldTimes[i] > 1.2) {
                if (_meterBridgeView->inHolds[i] > 0.0f) {
                    _meterBridgeView->inHolds[i] = fmaxf(0.0f, _meterBridgeView->inHolds[i] - 0.04f);
                }
            }

            if (db >= -0.2f) {
                _meterBridgeView->inClipTimes[i] = now;
            }
        }

        // Animate Output Meters
        for (int i = 0; i < 2; i++) {
            float raw = _shm->out_peak[i];
            float db = PeakToDb(raw);
            float norm = DbToNorm(db);
            _masterView->outDbs[i] = db;

            if (norm > _masterView->outVals[i]) {
                _masterView->outVals[i] = norm;
            } else {
                _masterView->outVals[i] *= 0.86f;
                if (_masterView->outVals[i] < 0.002f) _masterView->outVals[i] = 0.0f;
            }

            if (norm >= _masterView->outHolds[i]) {
                _masterView->outHolds[i] = norm;
                _masterView->outHoldTimes[i] = now;
            } else if (now - _masterView->outHoldTimes[i] > 1.2) {
                if (_masterView->outHolds[i] > 0.0f) {
                    _masterView->outHolds[i] = fmaxf(0.0f, _masterView->outHolds[i] - 0.04f);
                }
            }

            if (db >= -0.2f) {
                _masterView->outClipTimes[i] = now;
            }
        }

        [_meterBridgeView setNeedsDisplay:YES];
        [_masterView setNeedsDisplay:YES];
    }
}

- (void)windowWillClose:(NSNotification *)notification {
    [NSApp terminate:nil];
}

@end

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];

        TascamConsoleController *controller = [[TascamConsoleController alloc] init];
        [app setDelegate:controller];
        [app run];
    }
    return 0;
}

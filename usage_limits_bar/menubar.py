"""macOS menu bar app for Claude and Codex usage limits."""

import math
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAttributedString,
    NSBezierPath,
    NSButton,
    NSColor,
    NSEventTrackingRunLoopMode,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSImage,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSStatusBar,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
    NSTextAlignmentRight,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingMouseEnteredAndExited,
    NSVariableStatusItemLength,
    NSView,
)
from Foundation import (
    NSDefaultRunLoopMode,
    NSObject,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSTimer,
)
from PyObjCTools import AppHelper

from .limits import (
    CLAUDE,
    CODEX,
    PROVIDER_NAMES,
    PROVIDERS,
    CredentialsNotFound,
    TokenRejected,
    UsageRateLimited,
    fetch_usage,
    get_credentials,
    load_cache,
    parse_limits,
    primary_limit,
    reset_label,
    save_cache,
    time_until,
)
from .update import CHECK_INTERVAL_SECONDS, RELEASES_URL, available_update

REFRESH_SECONDS = 60
SPIN_FPS = 30
SPIN_STEP_DEGREES = 12
SPIN_MIN_SECONDS = 0.6
MENU_WIDTH = 264
RATE_LIMIT_ERROR = "Usage API rate-limited — showing last known data"
RATE_LIMIT_NO_DATA_ERROR = "Usage API rate-limited — retrying in a minute"
DONATE_URL = "https://base.monobank.ua/tilbertbalaban"
CLAUDE_USAGE_PAGE = "https://claude.ai/settings/usage"
CODEX_USAGE_PAGE = "https://chatgpt.com/codex/settings/usage"

GREEN = NSColor.systemGreenColor()
PURPLE = NSColor.systemPurpleColor()
BLUE = NSColor.systemBlueColor()
TEAL = NSColor.systemTealColor()
ORANGE = NSColor.systemOrangeColor()
RED = NSColor.systemRedColor()


def ring_color(limit):
    if limit.exhausted:
        return RED
    if limit.warning:
        return ORANGE
    colors = {
        CLAUDE: (GREEN, PURPLE),
        CODEX: (BLUE, TEAL),
    }
    primary, secondary = colors.get(limit.provider, (GREEN, PURPLE))
    return primary if limit.kind == "session" else secondary


def draw_ring(center, radius, line_width, percent, color, track_color, spin=0):
    track = NSBezierPath.bezierPath()
    track.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
        center, radius, 0, 360)
    track.setLineWidth_(line_width)
    track_color.setStroke()
    track.stroke()
    fraction = min(max(percent, 0), 100) / 100.0
    if fraction <= 0:
        return
    arc = NSBezierPath.bezierPath()
    # Start at 12 o'clock and sweep clockwise, like a countdown dial.
    arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
        center, radius, 90 - spin, 90 - spin - 360 * fraction, True)
    arc.setLineWidth_(line_width)
    arc.setLineCapStyle_(1)  # round
    color.setStroke()
    arc.stroke()


def text_attrs(size, color, bold=False, align=NSTextAlignmentCenter):
    style = NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(align)
    font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
    return {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: color,
        NSParagraphStyleAttributeName: style,
    }


def draw_text(text, rect, attrs):
    s = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
    h = s.size().height
    s.drawInRect_(NSMakeRect(rect.origin.x,
                             rect.origin.y + (rect.size.height - h) / 2.0,
                             rect.size.width, h))


def status_bar_image(limits, dark):
    """Two mini rings with the percent inside, like the reference app."""
    size = 18
    gap = 3
    count = max(len(limits), 1)
    img = NSImage.alloc().initWithSize_((count * size + (count - 1) * gap, 20))
    img.lockFocus()
    number_color = NSColor.whiteColor() if dark else NSColor.blackColor()
    track = (NSColor.whiteColor() if dark else NSColor.blackColor()).colorWithAlphaComponent_(0.18)
    for i, limit in enumerate(limits):
        x = i * (size + gap)
        center = (x + size / 2.0, 1 + size / 2.0)
        draw_ring(center, size / 2.0 - 1.5, 2.5, limit.percent, ring_color(limit), track)
        pct = round(limit.percent)
        label = "!" if pct >= 100 else str(pct)
        draw_text(label, NSMakeRect(x, 1, size, size),
                  text_attrs(7.5, number_color, bold=True))
    img.unlockFocus()
    return img


def refresh_icon(degrees=0):
    """Template refresh glyph sized and weighted like the SF Symbols next to
    it: a 300° arc with an arrowhead, drawn about the exact canvas center so
    spinning it never wobbles (the SF Symbol's arrowhead makes it orbit)."""
    side = 15.0
    c = side / 2.0
    r = 5.3

    def draw(_rect):
        NSColor.blackColor().set()
        start, end = 340 - degrees, 40 - degrees   # 300° sweep, gap at top-right
        arc = NSBezierPath.bezierPath()
        arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (c, c), r, start, end, True)
        arc.setLineWidth_(1.35)
        arc.setLineCapStyle_(1)
        arc.stroke()
        a = math.radians(end)
        tip_x, tip_y = c + r * math.cos(a), c + r * math.sin(a)
        tx, ty = math.sin(a), -math.cos(a)          # clockwise tangent
        nx, ny = math.cos(a), math.sin(a)           # outward normal
        head = NSBezierPath.bezierPath()
        head.moveToPoint_((tip_x + tx * 2.4, tip_y + ty * 2.4))
        head.lineToPoint_((tip_x + nx * 2.0 - tx * 0.5, tip_y + ny * 2.0 - ty * 0.5))
        head.lineToPoint_((tip_x - nx * 2.0 - tx * 0.5, tip_y - ny * 2.0 - ty * 0.5))
        head.closePath()
        head.fill()
        return True

    # A drawing handler keeps the glyph vector, so it is crisp on Retina.
    out = NSImage.imageWithSize_flipped_drawingHandler_((side, side), False, draw)
    out.setTemplate_(True)
    return out


def _symbol_button(symbol, fallback, tooltip, target, action):
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        symbol, tooltip)
    if image is not None:
        button = NSButton.buttonWithImage_target_action_(image, target, action)
    else:
        button = NSButton.buttonWithTitle_target_action_(fallback, target, action)
    button.setBordered_(False)
    return button


class HeaderView(NSView):
    """App title with the stats, donate ($) and refresh icon buttons.

    Hovering a button shows its hint in place of the title. The hint clears
    on a short delay so moving between adjacent buttons swaps hints without
    the title flashing in between.
    """

    BUTTONS = [
        ("dollarsign.circle", "$", "Support the developer", "donate:"),
        ("chart.bar.xaxis", "C", "Open Claude usage", "openClaudeUsage:"),
        ("chart.bar.fill", "X", "Open Codex usage", "openCodexUsage:"),
        ("arrow.clockwise", "↻", "Refresh", "refresh:"),
    ]
    CLEAR_DELAY = 0.25

    def initWithTarget_(self, target):
        self = objc.super(HeaderView, self).initWithFrame_(
            NSMakeRect(0, 0, MENU_WIDTH, 38))
        if self is None:
            return None
        self._hint = None
        x = MENU_WIDTH - 30 * len(self.BUTTONS) - 10
        for symbol, fallback, hint, action in self.BUTTONS:
            button = _symbol_button(symbol, fallback, hint, target, action)
            frame = NSMakeRect(x, 7, 26, 24)
            button.setFrame_(frame)
            self.addSubview_(button)
            if action == "refresh:":
                self.refresh_button = button
                self.refresh_image = refresh_icon(0)
                button.setImage_(self.refresh_image)
            self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                frame,
                NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways,
                self, {"hint": hint}))
            x += 30
        return self

    @objc.python_method
    def set_spin(self, degrees):
        self.refresh_button.setImage_(
            self.refresh_image if degrees == 0 else refresh_icon(degrees))

    def mouseEntered_(self, event):
        NSObject.cancelPreviousPerformRequestsWithTarget_(self)
        hint = event.trackingArea().userInfo()["hint"]
        if hint != self._hint:
            self._hint = hint
            self.setNeedsDisplay_(True)

    def mouseExited_(self, _event):
        # Menus track in NSEventTrackingRunLoopMode; a default-mode delayed
        # perform would never fire while the menu is open.
        self.performSelector_withObject_afterDelay_inModes_(
            "clearHint:", None, self.CLEAR_DELAY,
            [NSEventTrackingRunLoopMode, NSDefaultRunLoopMode])

    def clearHint_(self, _arg):
        self._hint = None
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        if self._hint:
            draw_text(self._hint, NSMakeRect(16, 3, MENU_WIDTH - 110, 32),
                      text_attrs(12, NSColor.secondaryLabelColor(),
                                 align=NSTextAlignmentLeft))
        else:
            draw_text("Usage Limits", NSMakeRect(16, 3, MENU_WIDTH - 140, 32),
                      text_attrs(14, NSColor.labelColor(), bold=True,
                                 align=NSTextAlignmentLeft))


class DonutView(NSView):
    """Large session donut with the weekly limit as a thin outer arc."""

    def initWithProvider_limits_(self, provider, limits):
        self = objc.super(DonutView, self).initWithFrame_(
            NSMakeRect(0, 0, MENU_WIDTH, 170))
        if self is None:
            return None
        self._provider = provider
        self._limits = limits
        self._spin = 0
        return self

    @objc.python_method
    def set_spin(self, degrees):
        self._spin = degrees
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        session = next(
            (limit for limit in self._limits if limit.kind == "session"), None)
        weekly = next(
            (limit for limit in self._limits if limit.kind == "weekly_all"), None)
        primary = session or (self._limits[0] if self._limits else None)
        if primary is None:
            return
        center = (MENU_WIDTH / 2.0, 85)
        track = NSColor.labelColor().colorWithAlphaComponent_(0.12)
        draw_ring(center, 62, 11, primary.percent, ring_color(primary), track,
                  spin=self._spin)
        if weekly is not None:
            draw_ring(center, 74, 3.5, weekly.percent, ring_color(weekly),
                      NSColor.clearColor(), spin=self._spin)
        draw_text("%d%%" % round(primary.percent),
                  NSMakeRect(0, 78, MENU_WIDTH, 40),
                  text_attrs(28, NSColor.labelColor(), bold=True))
        draw_text(PROVIDER_NAMES[self._provider] + " · Used",
                  NSMakeRect(0, 54, MENU_WIDTH, 20),
                  text_attrs(12, NSColor.secondaryLabelColor()))


class LimitRowView(NSView):
    """Mini ring + limit name + reset time, like the reference app's rows."""

    def initWithLimit_(self, limit):
        self = objc.super(LimitRowView, self).initWithFrame_(
            NSMakeRect(0, 0, MENU_WIDTH, 36))
        if self is None:
            return None
        self._limit = limit
        self._spin = 0
        return self

    @objc.python_method
    def set_spin(self, degrees):
        self._spin = degrees
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bg = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(10, 2, MENU_WIDTH - 20, 32), 8, 8)
        NSColor.labelColor().colorWithAlphaComponent_(0.06).setFill()
        bg.fill()
        limit = self._limit
        track = NSColor.labelColor().colorWithAlphaComponent_(0.15)
        draw_ring((20 + 9, 18), 7.5, 2.5, limit.percent, ring_color(limit), track,
                  spin=self._spin)
        pct = round(limit.percent)
        draw_text("!" if pct >= 100 else str(pct), NSMakeRect(20, 9, 18, 18),
                  text_attrs(6.5, NSColor.labelColor(), bold=True))
        draw_text(limit.label, NSMakeRect(46, 2, 92, 32),
                  text_attrs(13, NSColor.secondaryLabelColor(),
                             align=NSTextAlignmentLeft))
        draw_text(reset_label(limit.resets_at), NSMakeRect(120, 2, MENU_WIDTH - 136, 32),
                  text_attrs(13, NSColor.labelColor(), bold=True,
                             align=NSTextAlignmentRight))


class ErrorRowView(NSView):
    """Fixed-width warning row so long messages never widen the menu."""

    def initWithMessage_(self, message):
        self = objc.super(ErrorRowView, self).initWithFrame_(
            NSMakeRect(0, 0, MENU_WIDTH, 34))
        if self is None:
            return None
        self._message = message
        return self

    def drawRect_(self, rect):
        attrs = text_attrs(11, NSColor.secondaryLabelColor(),
                           align=NSTextAlignmentLeft)
        NSAttributedString.alloc().initWithString_attributes_(
            "⚠️ " + self._message, attrs).drawInRect_(
            NSMakeRect(16, 2, MENU_WIDTH - 32, 30))


class StatusApp(NSObject):
    def applicationDidFinishLaunching_(self, _notification):
        self._limits = []
        for provider in PROVIDERS:
            cached = load_cache(provider)
            if cached:
                self._limits.extend(parse_limits(provider, cached))
        self._errors = {}
        self._skip_ticks = {provider: 0 for provider in PROVIDERS}
        self._update = None
        self._last_update_check = 0.0
        self._fetching = False
        self._fetch_started = 0.0
        self._spin = 0
        self._spin_timer = None
        self._animated_views = []
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength)
        self.status_item.button().setImagePosition_(2)  # NSImageLeft
        self.menu = NSMenu.alloc().init()
        self.status_item.setMenu_(self.menu)
        self._render()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            REFRESH_SECONDS, self, "tick:", None, True)
        self.tick_(None)

    def tick_(self, _timer):
        providers = []
        for provider in PROVIDERS:
            if self._skip_ticks[provider] > 0:
                self._skip_ticks[provider] -= 1
            else:
                providers.append(provider)
        if not providers:
            return
        self._fetching = True
        self._fetch_started = time.time()
        self._start_spin()
        threading.Thread(target=self._fetch, args=(providers,), daemon=True).start()

    @objc.python_method
    def _start_spin(self):
        if self._spin_timer is not None:
            return
        self._spin_timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / SPIN_FPS, self, "spin:", None, True)
        # Menus track in NSEventTrackingRunLoopMode; a default-mode timer
        # would freeze the animation while the menu is open.
        loop = NSRunLoop.currentRunLoop()
        loop.addTimer_forMode_(self._spin_timer, NSRunLoopCommonModes)
        loop.addTimer_forMode_(self._spin_timer, NSEventTrackingRunLoopMode)

    def spin_(self, _timer):
        self._spin = (self._spin + SPIN_STEP_DEGREES) % 360
        settled = (not self._fetching
                   and time.time() - self._fetch_started >= SPIN_MIN_SECONDS
                   and self._spin == 0)
        if settled:
            self._spin_timer.invalidate()
            self._spin_timer = None
        for view in self._animated_views:
            view.set_spin(self._spin)

    @objc.python_method
    def _fetch(self, providers):
        if time.time() - self._last_update_check > CHECK_INTERVAL_SECONDS:
            self._last_update_check = time.time()
            self._update = available_update()
        with ThreadPoolExecutor(max_workers=len(providers)) as pool:
            results = dict(zip(providers, pool.map(self._fetch_provider, providers)))
        AppHelper.callAfter(self._apply, results)

    @objc.python_method
    def _fetch_provider(self, provider):
        try:
            data = fetch_usage(provider, get_credentials(provider))
            save_cache(provider, data)
            return parse_limits(provider, data), None
        except CredentialsNotFound:
            command = "claude" if provider == CLAUDE else "codex login"
            return None, "No %s credentials — run `%s` and sign in" % (
                PROVIDER_NAMES[provider], command)
        except TokenRejected:
            command = "Claude Code" if provider == CLAUDE else "`codex login`"
            return None, "%s token expired — sign in with %s again" % (
                PROVIDER_NAMES[provider], command)
        except UsageRateLimited:
            has_data = any(limit.provider == provider for limit in self._limits)
            error = RATE_LIMIT_ERROR if has_data else RATE_LIMIT_NO_DATA_ERROR
            return None, PROVIDER_NAMES[provider] + ": " + error
        except Exception:
            host = "api.anthropic.com" if provider == CLAUDE else "chatgpt.com"
            return None, "Could not reach " + host

    @objc.python_method
    def _apply(self, results):
        for provider, (limits, error) in results.items():
            if limits is not None:
                self._limits = [
                    limit for limit in self._limits if limit.provider != provider
                ] + limits
            if error:
                self._errors[provider] = error
            else:
                self._errors.pop(provider, None)
            self._skip_ticks[provider] = 4 if (
                error and RATE_LIMIT_ERROR in error
            ) else 0
        self._fetching = False
        self._render()

    @objc.python_method
    def _render(self):
        button = self.status_item.button()
        bar_limits = [
            limit for limit in self._limits
            if limit.kind in ("session", "weekly_all")
        ] or self._limits[:2]
        primary = primary_limit(self._limits)
        if bar_limits:
            dark = "dark" in str(button.effectiveAppearance().name()).lower()
            button.setImage_(status_bar_image(bar_limits, dark))
            button.setTitle_(" " + time_until(primary.resets_at) if primary else "")
        else:
            button.setImage_(None)
            button.setTitle_("…" if not self._errors else "?")
        # Rebuilding replaces the menu's views, which cancels hover/tooltips
        # if the menu is open — skip it when nothing visible changed.
        state = (tuple((limit.provider, limit.label, round(limit.percent),
                        limit.resets_at, limit.severity)
                       for limit in self._limits),
                 tuple(sorted(self._errors.items())), self._update)
        if state != getattr(self, "_menu_state", None):
            self._menu_state = state
            self._rebuild_menu()

    @objc.python_method
    def _rebuild_menu(self):
        self.menu.removeAllItems()
        self._animated_views = []
        header = HeaderView.alloc().initWithTarget_(self)
        header_item = NSMenuItem.alloc().init()
        header_item.setView_(header)
        self.menu.addItem_(header_item)
        self._animated_views.append(header)
        rendered_provider = False
        for provider in PROVIDERS:
            provider_limits = [
                limit for limit in self._limits if limit.provider == provider
            ]
            if rendered_provider and (provider_limits or provider in self._errors):
                self.menu.addItem_(NSMenuItem.separatorItem())
            if provider_limits:
                donut = DonutView.alloc().initWithProvider_limits_(
                    provider, provider_limits)
                donut_item = NSMenuItem.alloc().init()
                donut_item.setView_(donut)
                self.menu.addItem_(donut_item)
                self._animated_views.append(donut)
                for limit in provider_limits:
                    row_view = LimitRowView.alloc().initWithLimit_(limit)
                    row = NSMenuItem.alloc().init()
                    row.setView_(row_view)
                    self.menu.addItem_(row)
                    self._animated_views.append(row_view)
            if provider in self._errors:
                err = NSMenuItem.alloc().init()
                err.setView_(ErrorRowView.alloc().initWithMessage_(
                    self._errors[provider]))
                self.menu.addItem_(err)
            rendered_provider = rendered_provider or bool(
                provider_limits or provider in self._errors)
        for view in self._animated_views:
            view.set_spin(self._spin)
        if self._update:
            self.menu.addItem_(NSMenuItem.separatorItem())
            self._add_action("Update available — v" + self._update, "openReleases:")
        self.menu.addItem_(NSMenuItem.separatorItem())
        self._add_action("Quit", "quit:")

    @objc.python_method
    def _add_action(self, title, selector):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, selector, "")
        item.setTarget_(self)
        self.menu.addItem_(item)

    def refresh_(self, _sender):
        self._skip_ticks = {provider: 0 for provider in PROVIDERS}
        self.tick_(None)

    def openClaudeUsage_(self, _sender):
        self.menu.cancelTracking()
        webbrowser.open(CLAUDE_USAGE_PAGE)

    def openCodexUsage_(self, _sender):
        self.menu.cancelTracking()
        webbrowser.open(CODEX_USAGE_PAGE)

    def donate_(self, _sender):
        self.menu.cancelTracking()
        webbrowser.open(DONATE_URL)

    def openReleases_(self, _sender):
        self.menu.cancelTracking()
        webbrowser.open(RELEASES_URL)

    def quit_(self, _sender):
        NSApplication.sharedApplication().terminate_(None)


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = StatusApp.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()

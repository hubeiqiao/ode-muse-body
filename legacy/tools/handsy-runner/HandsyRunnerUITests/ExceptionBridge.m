#import "ExceptionBridge.h"

NSString *HSPerform(void (^block)(void)) {
    @try { block(); return nil; }
    @catch (NSException *exception) { return exception.name; }
}

BOOL HSKeyboardFocused(XCUIElement *element) {
    // iOS keyboard focus is not the tvOS-style public hasFocus attribute.
    // XCTest exposes this runtime key on supported Xcode versions. Fail closed.
    @try { return [[element valueForKey:@"hasKeyboardFocus"] boolValue]; }
    @catch (NSException *exception) { return NO; }
}

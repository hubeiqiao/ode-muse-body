#import <Foundation/Foundation.h>
#import <XCTest/XCTest.h>
NS_ASSUME_NONNULL_BEGIN
FOUNDATION_EXPORT NSString * _Nullable HSPerform(NS_NOESCAPE void (^block)(void));
FOUNDATION_EXPORT BOOL HSKeyboardFocused(XCUIElement *element);
NS_ASSUME_NONNULL_END

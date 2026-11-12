ARCHS = armv7
TARGET = iphone:clang:10.3:9.0
DEBUG = 0
FINALPACKAGE = 1

include $(THEOS)/makefiles/common.mk

APPLICATION_NAME = AdaOSLab
AdaOSLab_FILES = main.m LabHTTPSProbe.m LabSecureStorageProbe.m LabSessionClient.m LabNativeProbe.m LabAudioProbe.m AdaOnboarding.c AdaOnboardingStore.m AdaOnboardingViewController.m AdaAppStorage.m AdaJSONRequest.m AdaSubnetSession.m AdaWebIOTransport.m AdaSubnetClient.m AdaConversationViewController.m AdaClientSelfTest.m AdaClientUIProbe.m AdaSpeechOutput.m AdaLocalUIProbe.m
AdaOSLab_FILES += AdaVoiceDetector.c AdaVoiceMetrics.c AdaVoiceGate.c AdaVoiceSegmentBuffer.m AdaVoiceActivation.m AdaVoiceSetupStore.m AdaVoiceUIProbe.m
AdaOSLab_FRAMEWORKS = UIKit Foundation Security AVFoundation WebKit CoreImage AudioToolbox
# Archived SDK module-cache signatures are unstable across isolated builds.
AdaOSLab_CFLAGS = -fobjc-arc -Wall -Wextra -fno-modules
AdaOSLab_CODESIGN_FLAGS = -Sentitlements.plist

include $(THEOS_MAKE_PATH)/application.mk

# Device installation is deliberately separate from compilation/package creation.

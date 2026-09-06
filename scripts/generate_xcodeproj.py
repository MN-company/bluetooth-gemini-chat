#!/usr/bin/env python3
"""Generate a minimal Xcode project for GeminiAirBridge iOS app from an SPM package."""

import plistlib
import uuid
import os
import sys

def uid():
    return uuid.uuid4().hex.upper()[:24]

PROJ_DIR = "GeminiAirBridge-iOS.xcodeproj"
SRC = "Sources/GeminiAirBridge"

files = [
    ("GeminiAirBridgeApp.swift", f"{SRC}/App/GeminiAirBridgeApp.swift"),
    ("BLEServerManager.swift",  f"{SRC}/BLE/BLEServerManager.swift"),
    ("FrameCodec.swift",        f"{SRC}/BLE/FrameCodec.swift"),
    ("BLEConstants.swift",      f"{SRC}/BLE/BLEConstants.swift"),
    ("BridgeService.swift",     f"{SRC}/Gemini/BridgeService.swift"),
    ("GeminiApiClient.swift",   f"{SRC}/Gemini/GeminiApiClient.swift"),
    ("PromptModels.swift",      f"{SRC}/Gemini/PromptModels.swift"),
    ("ContentView.swift",       f"{SRC}/UI/ContentView.swift"),
    ("SettingsView.swift",      f"{SRC}/UI/SettingsView.swift"),
    ("ContainerStore.swift",    f"{SRC}/Container/ContainerStore.swift"),
]

# Create UUIDs
bf_ids = {name: uid() for name, _ in files}
fr_ids = {name: uid() for name, _ in files}
infoplist_fr = uid()
infoplist_bf = uid()
product_ref = uid()
native_target = uid()

groups = {}

def make_build_file(file_ref):
    return {"isa": "PBXBuildFile", "fileRef": file_ref}

def make_file_ref(path):
    return {
        "isa": "PBXFileReference",
        "lastKnownFileType": "sourcecode.swift",
        "path": os.path.basename(path),
        "sourceTree": "<group>",
    }

# Objects dict
objects = {}

# Build files & File references
for name, path in files:
    objects[bf_ids[name]] = make_build_file(fr_ids[name])
    objects[fr_ids[name]] = make_file_ref(path)

# Info.plist
objects[infoplist_fr] = {
    "isa": "PBXFileReference",
    "lastKnownFileType": "text.plist.xml",
    "path": "Info.plist",
    "sourceTree": "<group>",
}
objects[infoplist_bf] = {"isa": "PBXBuildFile", "fileRef": infoplist_fr}

# Product ref
objects[product_ref] = {
    "isa": "PBXFileReference",
    "explicitFileType": "wrapper.application",
    "includeInIndex": 0,
    "path": "GeminiAirBridge.app",
    "sourceTree": "BUILT_PRODUCTS_DIR",
}

# Subdir groups for source files
def make_group(name, file_list):
    gid = uid()
    children = []
    for fname in file_list:
        if fname in fr_ids:
            children.append(fr_ids[fname])
    groups[name] = gid
    objects[gid] = {
        "isa": "PBXGroup",
        "children": children,
        "name": name,
        "sourceTree": "<group>",
    }

make_group("BLE",    ["BLEServerManager.swift", "FrameCodec.swift", "BLEConstants.swift"])
make_group("Gemini", ["BridgeService.swift", "GeminiApiClient.swift", "PromptModels.swift"])
make_group("UI",     ["ContentView.swift", "SettingsView.swift"])
make_group("Container", ["ContainerStore.swift"])
make_group("App",    ["GeminiAirBridgeApp.swift"])

# Source group (contains sub-groups + Info.plist)
source_group = uid()
objects[source_group] = {
    "isa": "PBXGroup",
    "children": list(groups.values()) + [infoplist_fr],
    "name": "Sources",
    "sourceTree": "<group>",
}

# Products group
prod_group = uid()
objects[prod_group] = {
    "isa": "PBXGroup",
    "children": [product_ref],
    "name": "Products",
    "sourceTree": "<group>",
}

# Root group
root_group = uid()
objects[root_group] = {
    "isa": "PBXGroup",
    "children": [source_group, prod_group],
    "sourceTree": "<group>",
}

# Build phases
src_phase = uid()
objects[src_phase] = {
    "isa": "PBXSourcesBuildPhase",
    "buildActionMask": 2147483647,
    "files": [{"isa": "PBXBuildFile", "fileRef": fr_ids[name]} for name, _ in files],
    "runOnlyForDeploymentPostprocessing": 0,
}

fw_phase = uid()
objects[fw_phase] = {
    "isa": "PBXFrameworksBuildPhase",
    "buildActionMask": 2147483647,
    "files": [],
    "runOnlyForDeploymentPostprocessing": 0,
}

res_phase = uid()
objects[res_phase] = {
    "isa": "PBXResourcesBuildPhase",
    "buildActionMask": 2147483647,
    "files": [{"isa": "PBXBuildFile", "fileRef": infoplist_fr}],
    "runOnlyForDeploymentPostprocessing": 0,
}

# Native target
target_cfg_list = uid()
objects[target_cfg_list] = {
    "isa": "XCConfigurationList",
    "buildConfigurations": [
        {"isa": "XCBuildConfiguration", "name": "Debug",
         "buildSettings": {
             "ASSETCATALOG_COMPILER_APPICON_NAME": "",
             "CODE_SIGNING_ALLOWED": "NO",
             "CODE_SIGN_STYLE": "Manual",
             "GCC_OPTIMIZATION_LEVEL": "0",
             "INFOPLIST_FILE": "Info.plist",
             "IPHONEOS_DEPLOYMENT_TARGET": "17.0",
             "LD_RUNPATH_SEARCH_PATHS": ["$(inherited)", "@executable_path/Frameworks"],
             "PRODUCT_BUNDLE_IDENTIFIER": "com.mncompany.geminiairbridge",
             "PRODUCT_NAME": "GeminiAirBridge",
             "SDKROOT": "iphoneos",
             "SWIFT_ACTIVE_COMPILER_CONDITIONS": ["$(inherited)"],
             "SWIFT_STRICT_CONCURRENCY": "complete",
             "SWIFT_VERSION": "6.0",
             "TARGETED_DEVICE_FAMILY": "1,2",
             "OTHER_LDFLAGS": ["$(inherited)", "-framework", "CoreBluetooth"],
         }},
        {"isa": "XCBuildConfiguration", "name": "Release",
         "buildSettings": {
             "ASSETCATALOG_COMPILER_APPICON_NAME": "",
             "CODE_SIGNING_ALLOWED": "NO",
             "CODE_SIGN_STYLE": "Manual",
             "GCC_OPTIMIZATION_LEVEL": "s",
             "INFOPLIST_FILE": "Info.plist",
             "IPHONEOS_DEPLOYMENT_TARGET": "17.0",
             "LD_RUNPATH_SEARCH_PATHS": ["$(inherited)", "@executable_path/Frameworks"],
             "PRODUCT_BUNDLE_IDENTIFIER": "com.mncompany.geminiairbridge",
             "PRODUCT_NAME": "GeminiAirBridge",
             "SDKROOT": "iphoneos",
             "SWIFT_ACTIVE_COMPILER_CONDITIONS": ["$(inherited)"],
             "SWIFT_OPTIMIZATION_LEVEL": "-Owholemodule",
             "SWIFT_STRICT_CONCURRENCY": "complete",
             "SWIFT_VERSION": "6.0",
             "TARGETED_DEVICE_FAMILY": "1,2",
             "OTHER_LDFLAGS": ["$(inherited)", "-framework", "CoreBluetooth"],
             "VALIDATE_PRODUCT": "YES",
         }},
    ],
    "defaultConfigurationIsVisible": 0,
    "defaultConfigurationName": "Release",
}

objects[native_target] = {
    "isa": "PBXNativeTarget",
    "buildConfigurationList": target_cfg_list,
    "buildPhases": [src_phase, fw_phase, res_phase],
    "buildRules": [],
    "dependencies": [],
    "name": "GeminiAirBridge",
    "productName": "GeminiAirBridge",
    "productReference": product_ref,
    "productType": "com.apple.product-type.application",
}

# Project config list
proj_cfg_list = uid()
objects[proj_cfg_list] = {
    "isa": "XCConfigurationList",
    "buildConfigurations": [
        {"isa": "XCBuildConfiguration", "name": "Debug",
         "buildSettings": {
             "ALWAYS_SEARCH_USER_PATHS": "NO",
             "CLANG_ENABLE_MODULES": "YES",
             "CLANG_ENABLE_OBJC_ARC": "YES",
             "COPY_PHASE_STRIP": "NO",
             "IPHONEOS_DEPLOYMENT_TARGET": "17.0",
             "SDKROOT": "iphoneos",
             "SWIFT_ACTIVE_COMPILER_CONDITIONS": ["DEBUG"],
             "SWIFT_OPTIMIZATION_LEVEL": "-Onone",
         }},
        {"isa": "XCBuildConfiguration", "name": "Release",
         "buildSettings": {
             "ALWAYS_SEARCH_USER_PATHS": "NO",
             "CLANG_ENABLE_MODULES": "YES",
             "CLANG_ENABLE_OBJC_ARC": "YES",
             "COPY_PHASE_STRIP": "NO",
             "IPHONEOS_DEPLOYMENT_TARGET": "17.0",
             "SDKROOT": "iphoneos",
             "SWIFT_OPTIMIZATION_LEVEL": "-Owholemodule",
             "VALIDATE_PRODUCT": "YES",
         }},
    ],
    "defaultConfigurationIsVisible": 0,
    "defaultConfigurationName": "Release",
}

# Project
project_id = uid()
objects[project_id] = {
    "isa": "PBXProject",
    "attributes": {
        "BuildIndependentTargetsInParallel": 1,
        "LastSwiftUpdateCheck": 1640,
        "LastUpgradeCheck": 1640,
    },
    "buildConfigurationList": proj_cfg_list,
    "compatibilityVersion": "Xcode 14.0",
    "developmentRegion": "en",
    "hasScannedForEncodings": 0,
    "knownRegions": ["en", "Base"],
    "mainGroup": root_group,
    "productRefGroup": prod_group,
    "projectDirPath": "",
    "projectRoot": "",
    "targets": [native_target],
}

root = {
    "archiveVersion": 1,
    "classes": {},
    "objectVersion": 56,
    "objects": objects,
    "rootObject": project_id,
}

os.makedirs(PROJ_DIR, exist_ok=True)
with open(f"{PROJ_DIR}/project.pbxproj", "wb") as f:
    plistlib.dump(root, f, fmt=plistlib.FMT_XML)

print(f"Generated {PROJ_DIR}/project.pbxproj")
# AdaOS Orbit (beta)

AdaOS Orbit is the native companion application for AdaOS. It provides chat,
notifications, voice controls and access to a subnet. On Windows it can also
install and manage a local node. Orbit was previously named AdaOS Desktop;
the AdaOS browser workspace remains a separate interface.

Download Orbit from the download button at [inimatic.com](https://inimatic.com).
Choose your platform and processor. The Windows beta is a ZIP containing the
application: extract it to a writable folder and run `adaos.exe`.
The application does not require a separate Python, Go or browser runtime.
Installing a node downloads its own dependencies and requires more disk space.

## Connect and use Orbit

Without a local node, connect Orbit to an existing subnet using the approval
flow shown in the application. With a local node, Orbit follows that node's
state and provides its local controls. Closing Orbit leaves the node running.
Chat, notifications and voice features depend on the connected subnet and
its installed capabilities.

## Updates

Windows and the Linux user installation check for signed beta updates after
startup and then daily. In Settings, download an available update and choose
the restart action. The helper checks the package and retains a rollback copy.
Your existing profile and connection settings are preserved when updating
from AdaOS Desktop to AdaOS Orbit.

## Platform status

Windows is the primary installation acceptance target. Linux has a native
user installation, and Android and macOS have previews with platform-specific
limits described in the download dialogue. The beta packages are not a claim
of complete hardware or platform signing qualification.

param([string]$InitialDirectory = '')

Add-Type -AssemblyName System.Windows.Forms

if (-not ('Veo3FolderPicker.NativeFolderPicker' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace Veo3FolderPicker
{
    [Flags]
    public enum FileOpenOptions : uint
    {
        PickFolders = 0x00000020,
        ForceFileSystem = 0x00000040,
        PathMustExist = 0x00000800,
        NoChangeDirectory = 0x00000008
    }

    public enum ShellItemDisplayName : uint { FileSystemPath = 0x80058000 }

    [ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
    internal class FileOpenDialogCom { }

    [ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IFileDialog
    {
        [PreserveSig] int Show(IntPtr parent);
        void SetFileTypes(uint count, IntPtr filters);
        void SetFileTypeIndex(uint index);
        void GetFileTypeIndex(out uint index);
        void Advise(IntPtr events, out uint cookie);
        void Unadvise(uint cookie);
        void SetOptions(FileOpenOptions options);
        void GetOptions(out FileOpenOptions options);
        void SetDefaultFolder(IShellItem folder);
        void SetFolder(IShellItem folder);
        void GetFolder(out IShellItem folder);
        void GetCurrentSelection(out IShellItem item);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void GetResult(out IShellItem item);
        void AddPlace(IShellItem item, int alignment);
        void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string extension);
        void Close(int result);
        void SetClientGuid(ref Guid guid);
        void ClearClientData();
        void SetFilter(IntPtr filter);
    }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IShellItem
    {
        void BindToHandler(IntPtr context, ref Guid handler, ref Guid iid, out IntPtr result);
        void GetParent(out IShellItem parent);
        void GetDisplayName(ShellItemDisplayName name, out IntPtr value);
        void GetAttributes(uint mask, out uint attributes);
        void Compare(IShellItem other, uint hint, out int order);
    }

    public static class NativeFolderPicker
    {
        [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
        private static extern void SHCreateItemFromParsingName(
            string path, IntPtr context, ref Guid iid, out IShellItem item);

        public static string Pick(string initialDirectory, IntPtr owner)
        {
            IFileDialog dialog = (IFileDialog)new FileOpenDialogCom();
            try
            {
                dialog.SetOptions(FileOpenOptions.PickFolders | FileOpenOptions.ForceFileSystem |
                    FileOpenOptions.PathMustExist | FileOpenOptions.NoChangeDirectory);
                dialog.SetTitle("Ch\u1ECDn th\u01B0 m\u1EE5c \u1EA3nh v\u1EA3i");
                dialog.SetOkButtonLabel("Ch\u1ECDn th\u01B0 m\u1EE5c");

                if (!String.IsNullOrWhiteSpace(initialDirectory))
                {
                    Guid iid = typeof(IShellItem).GUID;
                    IShellItem initial = null;
                    try
                    {
                        SHCreateItemFromParsingName(initialDirectory, IntPtr.Zero, ref iid, out initial);
                        dialog.SetFolder(initial);
                    }
                    catch { }
                    finally { if (initial != null) Marshal.ReleaseComObject(initial); }
                }

                if (dialog.Show(owner) != 0) return String.Empty;
                IShellItem selected = null;
                try
                {
                    dialog.GetResult(out selected);
                    IntPtr pointer;
                    selected.GetDisplayName(ShellItemDisplayName.FileSystemPath, out pointer);
                    try { return Marshal.PtrToStringUni(pointer) ?? String.Empty; }
                    finally { Marshal.FreeCoTaskMem(pointer); }
                }
                finally { if (selected != null) Marshal.ReleaseComObject(selected); }
            }
            finally { Marshal.ReleaseComObject(dialog); }
        }
    }
}
'@
}

$owner = New-Object System.Windows.Forms.Form
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Opacity = 0
$owner.Show()
$owner.Activate()
try {
    $selected = [Veo3FolderPicker.NativeFolderPicker]::Pick($InitialDirectory, $owner.Handle)
    if ($selected) {
        [Console]::OutputEncoding = [Text.Encoding]::UTF8
        Write-Output $selected
    }
}
finally {
    $owner.Close()
    $owner.Dispose()
}

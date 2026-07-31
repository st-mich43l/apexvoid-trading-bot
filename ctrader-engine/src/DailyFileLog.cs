namespace ApexVoid.CTraderFeed;

/// <summary>
/// Tees Console.Out / Console.Error to a host-mounted daily log file.
/// The service rotates the file itself at local midnight — no host logrotate.
/// </summary>
internal static class DailyFileLog
{
  private static DailyRotatingTextWriter? _writer;

  public static void Install()
  {
    var enabled = Environment.GetEnvironmentVariable("LOG_FILE_ENABLED");
    if (
      string.Equals(enabled, "0", StringComparison.OrdinalIgnoreCase)
      || string.Equals(enabled, "false", StringComparison.OrdinalIgnoreCase)
      || string.Equals(enabled, "no", StringComparison.OrdinalIgnoreCase)
    )
    {
      return;
    }

    var directory = Environment.GetEnvironmentVariable("LOG_DIR")
      ?? "/var/log/apexvoid";
    var fileName = Environment.GetEnvironmentVariable("LOG_FILE_NAME")
      ?? "ctrader-engine.log";
    var retentionRaw = Environment.GetEnvironmentVariable("LOG_RETENTION_DAYS");
    var retentionDays = 14;
    if (
      !string.IsNullOrWhiteSpace(retentionRaw)
      && int.TryParse(retentionRaw, out var parsed)
      && parsed > 0
    )
    {
      retentionDays = parsed;
    }

    try
    {
      Directory.CreateDirectory(directory);
      var path = Path.Combine(directory, fileName);
      _writer = new DailyRotatingTextWriter(
        path,
        retentionDays,
        Console.Out,
        Console.Error
      );
      Console.SetOut(_writer);
      Console.SetError(_writer);
      Console.Error.WriteLine(
        $"ctrader-feed INFO file logging enabled path={path} "
        + $"retention_days={retentionDays}"
      );
    }
    catch (Exception ex)
    {
      Console.Error.WriteLine(
        $"ctrader-feed WARNING file logging disabled path={directory}/{fileName} "
        + $"error={ex.Message}"
      );
    }
  }
}

internal sealed class DailyRotatingTextWriter : TextWriter
{
  private readonly object _gate = new();
  private readonly string _activePath;
  private readonly int _retentionDays;
  private readonly TextWriter _consoleOut;
  private readonly TextWriter _consoleError;
  private StreamWriter? _file;
  private DateOnly _currentDay;

  public DailyRotatingTextWriter(
    string activePath,
    int retentionDays,
    TextWriter consoleOut,
    TextWriter consoleError
  )
  {
    _activePath = activePath;
    _retentionDays = Math.Max(1, retentionDays);
    _consoleOut = consoleOut;
    _consoleError = consoleError;
    _currentDay = DateOnly.FromDateTime(DateTime.Now);
    OpenFileForTodayUnlocked();
  }

  public override System.Text.Encoding Encoding => System.Text.Encoding.UTF8;

  public override void Write(char value)
  {
    lock (_gate)
    {
      EnsureDayUnlocked();
      _consoleOut.Write(value);
      _file?.Write(value);
    }
  }

  public override void Write(string? value)
  {
    if (value is null)
    {
      return;
    }
    lock (_gate)
    {
      EnsureDayUnlocked();
      _consoleOut.Write(value);
      _file?.Write(value);
    }
  }

  public override void WriteLine(string? value)
  {
    lock (_gate)
    {
      EnsureDayUnlocked();
      _consoleOut.WriteLine(value);
      _file?.WriteLine(value);
      _file?.Flush();
    }
  }

  public override void Flush()
  {
    lock (_gate)
    {
      _consoleOut.Flush();
      _file?.Flush();
    }
  }

  protected override void Dispose(bool disposing)
  {
    if (disposing)
    {
      lock (_gate)
      {
        _file?.Dispose();
        _file = null;
      }
    }
    base.Dispose(disposing);
  }

  private void EnsureDayUnlocked()
  {
    var today = DateOnly.FromDateTime(DateTime.Now);
    if (today == _currentDay && _file is not null)
    {
      return;
    }
    RotateUnlocked(today);
  }

  private void RotateUnlocked(DateOnly today)
  {
    if (_file is not null)
    {
      _file.Flush();
      _file.Dispose();
      _file = null;
      // Rename the just-closed active file to yesterday's dated archive.
      var archive = DatedPath(_currentDay);
      try
      {
        if (File.Exists(_activePath))
        {
          if (File.Exists(archive))
          {
            File.Delete(archive);
          }
          File.Move(_activePath, archive);
        }
      }
      catch (Exception ex)
      {
        _consoleError.WriteLine(
          $"ctrader-feed WARNING log rotate failed archive={archive} error={ex.Message}"
        );
      }
    }
    _currentDay = today;
    OpenFileForTodayUnlocked();
    PruneUnlocked();
  }

  private void OpenFileForTodayUnlocked()
  {
    _file = new StreamWriter(
      new FileStream(
        _activePath,
        FileMode.Append,
        FileAccess.Write,
        FileShare.ReadWrite
      )
    )
    {
      AutoFlush = true,
    };
  }

  private string DatedPath(DateOnly day) =>
    $"{_activePath}.{day:yyyy-MM-dd}";

  private void PruneUnlocked()
  {
    var directory = Path.GetDirectoryName(_activePath);
    var prefix = Path.GetFileName(_activePath) + ".";
    if (string.IsNullOrWhiteSpace(directory) || !Directory.Exists(directory))
    {
      return;
    }
    var cutoff = DateOnly.FromDateTime(DateTime.Now.AddDays(-_retentionDays));
    foreach (var path in Directory.EnumerateFiles(directory, prefix + "*"))
    {
      var name = Path.GetFileName(path);
      var suffix = name.Length > prefix.Length
        ? name[prefix.Length..]
        : "";
      if (
        !DateOnly.TryParseExact(
          suffix,
          "yyyy-MM-dd",
          System.Globalization.CultureInfo.InvariantCulture,
          System.Globalization.DateTimeStyles.None,
          out var day
        )
      )
      {
        continue;
      }
      if (day >= cutoff)
      {
        continue;
      }
      try
      {
        File.Delete(path);
      }
      catch
      {
        // Best-effort prune; keep writing.
      }
    }
  }
}

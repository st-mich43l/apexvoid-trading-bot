namespace ApexVoid.CTraderFeed;

public sealed class HealthFile(string path)
{
  public void Touch()
  {
    var dir = Path.GetDirectoryName(path);
    if (!string.IsNullOrWhiteSpace(dir))
    {
      Directory.CreateDirectory(dir);
    }
    File.WriteAllText(path, DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString());
  }

  public static int Check(string path, TimeSpan maxAge)
  {
    if (!File.Exists(path))
    {
      return 1;
    }
    var age = DateTimeOffset.UtcNow - File.GetLastWriteTimeUtc(path);
    return age <= maxAge ? 0 : 1;
  }

  /// <summary>
  /// Like <see cref="Check"/>, but a missing file is healthy rather than a
  /// failure. For a heartbeat only auto-trade writes: no file means either
  /// auto-trade is disabled on this deployment, or the session is still in
  /// its startup grace period - neither is a fault. Once the file exists,
  /// staleness is judged exactly as strictly as the feed heartbeat.
  /// </summary>
  public static int CheckIfPresent(string path, TimeSpan maxAge) =>
    File.Exists(path) ? Check(path, maxAge) : 0;
}

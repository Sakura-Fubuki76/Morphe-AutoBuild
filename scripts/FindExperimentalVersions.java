import app.morphe.patcher.patch.*;
import java.io.File;
import java.util.*;

public class FindExperimentalVersions {
    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("Usage: FindExperimentalVersions <mpp> <package>");
            System.exit(1);
        }

        File mppFile = new File(args[0]);
        String targetPackage = args[1];

        Set<File> files = Collections.singleton(mppFile);
        Set<Patch<?>> patches = PatchKt.loadPatchesFromJar(files);

        Set<String> allVersions = new TreeSet<>((a, b) -> {
            String[] pa = a.split("[.\\-_]");
            String[] pb = b.split("[.\\-_]");
            int len = Math.min(pa.length, pb.length);
            for (int i = 0; i < len; i++) {
                int ca = tryParseInt(pa[i]);
                int cb = tryParseInt(pb[i]);
                if (ca != cb) return Integer.compare(ca, cb);
                int sc = pa[i].compareTo(pb[i]);
                if (sc != 0) return sc;
            }
            return Integer.compare(pa.length, pb.length);
        });

        for (Patch<?> patch : patches) {
            List<?> rawCompat = patch.getCompatibility();
            if (rawCompat == null) continue;
            for (Object cObj : rawCompat) {
                if (!(cObj instanceof Compatibility)) continue;
                Compatibility compat = (Compatibility) cObj;
                if (!targetPackage.equals(compat.getPackageName())) continue;
                for (AppTarget target : compat.getTargets()) {
                    if (target.getVersion() != null) {
                        allVersions.add(target.getVersion());
                    }
                }
            }
        }

        if (!allVersions.isEmpty()) {
            for (String v : allVersions) {
                System.out.println(v);
            }
        }
    }

    private static int tryParseInt(String s) {
        try { return Integer.parseInt(s); }
        catch (NumberFormatException e) { return 0; }
    }
}

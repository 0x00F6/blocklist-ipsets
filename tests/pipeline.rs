use firehol_mmdb::{
    FIELDS, Options, discover, generate, parse_network, source_commit_epoch, validate_record,
};
use libmaxminddb_rs::{Reader, Value};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::atomic::{AtomicU64, Ordering},
};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        static ID: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "firehol-test-{}-{}",
            std::process::id(),
            ID.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn write(&self, path: &str, content: &str) {
        let path = self.0.join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, content).unwrap();
    }
    fn output(&self) -> PathBuf {
        self.0.join("output.mmdb")
    }
    fn git(&self, args: &[&str]) {
        let result = Command::new("git")
            .arg("-C")
            .arg(&self.0)
            .args(args)
            .env("GIT_AUTHOR_NAME", "Fixture")
            .env("GIT_AUTHOR_EMAIL", "fixture@example.invalid")
            .env("GIT_COMMITTER_NAME", "Fixture")
            .env("GIT_COMMITTER_EMAIL", "fixture@example.invalid")
            .env("GIT_AUTHOR_DATE", "2026-10-04T05:49:31+0000")
            .env("GIT_COMMITTER_DATE", "2026-10-05T09:17:32+0000")
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
    }
    fn generate(&self) -> Vec<u8> {
        generate(
            &self.0,
            &self.output(),
            &Options {
                threads: 4,
                batch_size: 2,
                queue_size: 1,
                build_epoch: Some(1_791_191_852),
            },
        )
        .unwrap();
        fs::read(self.output()).unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn record(reader: &Reader<'_>, ip: &str) -> serde_json::Value {
    let value = reader.lookup_value(ip.parse().unwrap()).unwrap();
    validate_record(&value.to_owned_value()).unwrap();
    value.to_json()
}

#[test]
fn source_committer_date_is_stored_and_rebuilds_are_reproducible() {
    let f = Fixture::new();
    f.write("a.ipset", "192.0.2.1\n");
    f.git(&["init", "-q"]);
    f.git(&["add", "a.ipset"]);
    f.git(&["commit", "-q", "-m", "source snapshot"]);
    assert_eq!(source_commit_epoch(&f.0).unwrap(), 1_791_191_852);
    generate(&f.0, &f.output(), &Options::default()).unwrap();
    let first = fs::read(f.output()).unwrap();
    assert_eq!(
        Reader::from_bytes(&first).unwrap().metadata().build_epoch,
        1_791_191_852
    );
    generate(&f.0, &f.output(), &Options::default()).unwrap();
    assert_eq!(first, fs::read(f.output()).unwrap());
}

#[test]
fn source_timestamp_rejects_parent_git_repositories_and_missing_checkouts() {
    let f = Fixture::new();
    assert!(source_commit_epoch(&f.0).is_err());
    f.write("data/a.ipset", "192.0.2.1\n");
    f.git(&["init", "-q"]);
    f.git(&["add", "data/a.ipset"]);
    f.git(&["commit", "-q", "-m", "parent repository"]);
    assert!(
        source_commit_epoch(&f.0.join("data"))
            .unwrap_err()
            .to_string()
            .contains("parent repository")
    );
}

#[test]
fn normalizes_ipv4_ipv6_and_host_bits() {
    for (input, expected) in [
        ("192.0.2.1", "192.0.2.1/32"),
        ("2001:db8::1", "2001:db8::1/128"),
        ("192.0.2.9/24", "192.0.2.0/24"),
        ("2001:db8::1/32", "2001:db8::/32"),
    ] {
        assert_eq!(parse_network(input).unwrap().to_string(), expected);
    }
    for invalid in [
        "invalid",
        "192.0.2.1/33",
        "2001:db8::/129",
        "192.0.2.1 80",
        "",
    ] {
        assert!(parse_network(invalid).is_err());
    }
}

#[test]
fn prunes_all_country_subtrees_and_skips_symlinks() {
    let f = Fixture::new();
    f.write("a.ipset", "192.0.2.1\n");
    f.write("nested/b.netset", "198.51.100.0/24\n");
    f.write("nested/foo_country/bad.ipset", "invalid\n");
    f.write("bar_country/deeper/bad.netset", "invalid\n");
    f.write(".hidden/bad.ipset", "invalid\n");
    f.write("country/not-a-list.txt", "invalid\n");
    #[cfg(unix)]
    std::os::unix::fs::symlink(f.0.join("a.ipset"), f.0.join("alias.ipset")).unwrap();
    assert_eq!(discover(&f.0).unwrap().len(), 2);
    let bytes = f.generate();
    let reader = Reader::from_bytes(&bytes).unwrap();
    assert_eq!(
        record(&reader, "198.51.100.9")["files"],
        serde_json::json!(["nested/b.netset"])
    );
}

#[test]
fn deepmerge_keeps_duplicate_arrays_and_empty_placeholders() {
    let f = Fixture::new();
    f.write("a.ipset", "# Category: malware\n# Maintainer: A\n# Source File Date: Thu Mar 12 07:15:03 UTC 2026\n# Update Frequency: 30 mins\n192.0.2.1\n192.0.2.1\n");
    f.write("b.ipset", "# Category: abuse\n# Version: 3\n192.0.2.1\n");
    let bytes = f.generate();
    let reader = Reader::from_bytes(&bytes).unwrap();
    let value = record(&reader, "192.0.2.1");
    assert_eq!(
        value["files"],
        serde_json::json!(["a.ipset", "a.ipset", "b.ipset"])
    );
    assert_eq!(
        value["categories"],
        serde_json::json!(["malware", "malware", "abuse"])
    );
    assert_eq!(value["maintainers"], serde_json::json!(["A", "A", ""]));
    assert_eq!(value["versions"], serde_json::json!(["", "", "3"]));
    assert_eq!(value["source_file_dates"][0], "2026-03-12T07:15:03Z");
    assert!(value.get("update_frequencies").is_none());
    assert!(reader.lookup_value("192.0.2.2".parse().unwrap()).is_err());
}

#[test]
fn overlaps_keep_all_parent_child_and_duplicate_sources() {
    let f = Fixture::new();
    f.write(
        "a.netset",
        "# Category: attacks\n10.0.0.0/8\n10.1.0.0/16\n10.1.0.0/16\n",
    );
    f.write("b.netset", "# Category: spam\n10.1.2.0/24\n10.2.0.0/16\n");
    f.write("c.ipset", "# Category: malware\n10.1.2.3\n");
    let bytes = f.generate();
    let reader = Reader::from_bytes(&bytes).unwrap();
    for (ip, expected) in [
        ("10.3.4.5", vec!["a.netset"]),
        ("10.1.3.4", vec!["a.netset", "a.netset", "a.netset"]),
        (
            "10.1.2.9",
            vec!["a.netset", "a.netset", "a.netset", "b.netset"],
        ),
        (
            "10.1.2.3",
            vec!["a.netset", "a.netset", "a.netset", "b.netset", "c.ipset"],
        ),
        ("10.2.3.4", vec!["a.netset", "b.netset"]),
    ] {
        assert_eq!(
            record(&reader, ip)["files"],
            serde_json::json!(expected),
            "{ip}"
        );
    }
    assert!(reader.lookup_value("11.1.2.3".parse().unwrap()).is_err());
}

#[test]
fn ipv6_overlaps_and_broad_ipv6_do_not_leak_into_ipv4() {
    let f = Fixture::new();
    f.write("a.netset", "# Category: unroutable\n::/0\n2001:db8::/32\n");
    f.write("b.ipset", "2001:db8::1\n192.0.2.1\n");
    let bytes = f.generate();
    let reader = Reader::from_bytes(&bytes).unwrap();
    assert_eq!(
        record(&reader, "2001:db8::1")["files"],
        serde_json::json!(["a.netset", "a.netset", "b.ipset"])
    );
    assert_eq!(
        record(&reader, "2001:db8::2")["files"],
        serde_json::json!(["a.netset", "a.netset"])
    );
    assert_eq!(
        record(&reader, "192.0.2.1")["files"],
        serde_json::json!(["b.ipset"])
    );
    assert!(reader.lookup_value("192.0.2.2".parse().unwrap()).is_err());
}

#[test]
fn captures_mid_file_metadata_changes_bom_crlf_and_inline_comments() {
    let f = Fixture::new();
    f.write("a.ipset", "\u{feff}# Category: spam\r\n# Maintainer: A\r\n192.0.2.1 # note\r\n# Maintainer: B\r\n# Category: bots\r\n# Source File Date: 2026-10-05\r\n2001:db8::1");
    let bytes = f.generate();
    let reader = Reader::from_bytes(&bytes).unwrap();
    assert_eq!(
        record(&reader, "192.0.2.1")["maintainers"],
        serde_json::json!(["A"])
    );
    let value = record(&reader, "2001:db8::1");
    assert_eq!(value["maintainers"], serde_json::json!(["B"]));
    assert_eq!(value["categories"], serde_json::json!(["botnet"]));
    assert_eq!(
        value["source_file_dates"],
        serde_json::json!(["2026-10-05T00:00:00Z"])
    );
}

#[test]
fn malformed_input_does_not_replace_output_or_leave_sort_files() {
    let f = Fixture::new();
    f.write("a.ipset", "192.0.2.1\nnot-an-ip\n");
    f.write("output.mmdb", "previous database");
    let error = generate(
        &f.0,
        &f.output(),
        &Options {
            threads: 4,
            batch_size: 1,
            queue_size: 1,
            build_epoch: None,
        },
    )
    .unwrap_err();
    assert!(format!("{error:#}").contains("line 2"));
    assert_eq!(fs::read_to_string(f.output()).unwrap(), "previous database");
    assert!(fs::read_dir(&f.0).unwrap().all(|e| {
        !e.unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".firehol-build")
    }));
}

#[test]
fn disk_merge_preserves_every_contribution_across_more_than_fan_in_runs() {
    let f = Fixture::new();
    let data: String = (0..180).map(|i| format!("192.0.2.{}\n", i % 20)).collect();
    f.write("a.ipset", &data);
    let bytes = f.generate();
    let reader = Reader::from_bytes(&bytes).unwrap();
    for i in 0..20 {
        assert_eq!(
            record(&reader, &format!("192.0.2.{i}"))["files"]
                .as_array()
                .unwrap()
                .len(),
            9
        );
    }
}

#[test]
fn generated_overlap_counts_match_a_naive_reference_for_every_address() {
    let f = Fixture::new();
    let mut expected: BTreeMap<u8, Vec<String>> = BTreeMap::new();
    for file in 0..5 {
        let name = format!("{file}.netset");
        let mut lines = String::new();
        for n in 0..25u32 {
            let prefix = 25 + ((n * 7 + file) % 8) as u8;
            let network = parse_network(&format!(
                "192.0.2.{}/{}",
                (n * 13 + file * 19) % 256,
                prefix
            ))
            .unwrap();
            lines.push_str(&format!("{network}\n"));
            for ip in 0..=255u8 {
                if network.contains(&format!("192.0.2.{ip}").parse::<std::net::IpAddr>().unwrap()) {
                    expected.entry(ip).or_default().push(name.clone());
                }
            }
        }
        f.write(&name, &lines);
    }
    let bytes = f.generate();
    let reader = Reader::from_bytes(&bytes).unwrap();
    for ip in 0..=255u8 {
        let result = reader.lookup_value(format!("192.0.2.{ip}").parse().unwrap());
        if let Some(wanted) = expected.get(&ip) {
            let value = result.unwrap().to_json();
            let mut actual: Vec<_> = value["files"]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap().to_owned())
                .collect();
            let mut wanted = wanted.clone();
            actual.sort();
            wanted.sort();
            assert_eq!(actual, wanted, "192.0.2.{ip}");
        } else {
            assert!(result.is_err());
        }
    }
}

#[test]
fn rejects_empty_inputs_invalid_options_and_misaligned_metadata() {
    let f = Fixture::new();
    assert!(generate(&f.0, &f.output(), &Options::default()).is_err());
    f.write("a.ipset", "# comments only\n");
    assert!(generate(&f.0, &f.output(), &Options::default()).is_err());
    assert!(
        generate(
            Path::new("missing-directory"),
            &f.output(),
            &Options::default()
        )
        .is_err()
    );
    assert!(
        generate(
            &f.0,
            &f.output(),
            &Options {
                threads: 0,
                ..Options::default()
            }
        )
        .is_err()
    );
    let mut fields: BTreeMap<String, Value> = FIELDS
        .into_iter()
        .map(|key| (key.into(), Value::Array(vec![Value::Utf8("".into())])))
        .collect();
    fields.insert("files".into(), Value::Array(vec![]));
    assert!(validate_record(&Value::Map(fields)).is_err());
    assert!(validate_record(&Value::Utf8("bad".into())).is_err());
}

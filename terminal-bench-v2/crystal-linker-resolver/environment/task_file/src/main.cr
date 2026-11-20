require "./model"
require "./parser"
require "./linker"

# Command-line driver.
#
#   crlink <job-dir> <out-file>
#
# The job directory contains a link.list file naming the inputs in link order,
# one per line. Entries ending in .cro are objects, entries ending in .cra are
# archives. The linked image JSON is written to <out-file>.

def run(job_dir : String, out_file : String)
  list_path = File.join(job_dir, "link.list")
  unless File.exists?(list_path)
    STDERR.puts "missing link.list in #{job_dir}"
    exit 1
  end

  inputs = [] of ObjectFile | Archive

  File.read(list_path).lines.each do |raw|
    entry = raw.strip
    next if entry.empty? || entry.starts_with?("#")
    path = File.join(job_dir, entry)
    text = File.read(path)
    if entry.ends_with?(".cra")
      inputs << Parser.parse_archive(text, entry)
    else
      inputs << Parser.parse_object(text, entry)
    end
  end

  linker = Linker.new
  linker.link(inputs)
  File.write(out_file, linker.to_json_image)
end

if ARGV.size != 2
  STDERR.puts "usage: crlink <job-dir> <out-file>"
  exit 1
end

run(ARGV[0], ARGV[1])

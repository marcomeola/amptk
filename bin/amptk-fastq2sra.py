#!/usr/bin/env python

import sys, os, re, gzip, argparse, inspect, csv, shutil, edlib
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0,parentdir)
import lib.amptklib as amptklib
import lib.revcomp_lib as revcomp_lib
import lib.primer as primer
from Bio.SeqIO.QualityIO import FastqGeneralIterator

class MyFormatter(argparse.ArgumentDefaultsHelpFormatter):
    def __init__(self,prog):
        super(MyFormatter,self).__init__(prog,max_help_position=48)
class col:
    GRN = '\033[92m'
    END = '\033[0m'
    WARN = '\033[93m'

parser=argparse.ArgumentParser(prog='amptk-fastq2sra.py', usage="%(prog)s [options] -i folder",
    description='''Script to split FASTQ file from Ion, 454, or Illumina by barcode sequence into separate files for submission to SRA.  This script can take the BioSample worksheet from NCBI and create an SRA metadata file for submission.''',
    epilog="""Written by Jon Palmer (2015) nextgenusfs@gmail.com""",
    formatter_class=MyFormatter)

parser.add_argument('-i','--input', dest='FASTQ', required=True, help='Input FASTQ file or folder')
parser.add_argument('-o','--out', dest='out', default="sra", help='Basename for output folder/files')
parser.add_argument('--min_len', default=50, type=int, help='Minimum length of read to keep')
parser.add_argument('-b','--barcode_fasta', dest='barcodes', help='Multi-fasta file containing barcodes used')
parser.add_argument('-s','--biosample', dest='biosample', help='BioSample file from NCBI')
parser.add_argument('-p','--platform', dest='platform', default='ion', choices=['ion', 'illumina', '454'], help='Sequencing platform')
parser.add_argument('-f','--fwd_primer', dest="F_primer", default='fITS7', help='Forward Primer (fITS7)')
parser.add_argument('-r','--rev_primer', dest="R_primer", default='ITS4', help='Reverse Primer (ITS4)')
parser.add_argument('-n', '--names', help='CSV mapping file BC,NewName')
parser.add_argument('-d', '--description', help='Paragraph description for SRA metadata')
parser.add_argument('-t','--title', default='Fungal ITS', help='Start of title for SRA submission, name it according to amplicon')
parser.add_argument('-m','--mapping_file', help='Mapping file: QIIME format can have extra meta data columns')
parser.add_argument('--primer_mismatch', default=2, type=int, help='Number of mis-matches in primer')
parser.add_argument('--require_primer', default='off', choices=['forward', 'both', 'off'], help='Require Primers to be present')
parser.add_argument('--force', action='store_true', help='Overwrite existing directory')
parser.add_argument('-a','--append', help='Append a name to all sample names for a run, i.e. --append run1 would yield Sample_run1')
args=parser.parse_args()

def FindBarcode(Seq, BarcodeDict):
    for BarcodeLabel in BarcodeDict.keys():
        Barcode = BarcodeDict[BarcodeLabel]
        if Seq.startswith(Barcode):
            return Barcode, BarcodeLabel
    return "", ""

log_name = args.out + '.amptk-sra.log'
if os.path.isfile(log_name):
    os.remove(log_name)

amptklib.setupLogging(log_name)
FNULL = open(os.devnull, 'w')
cmd_args = " ".join(sys.argv)+'\n'
amptklib.log.debug(cmd_args)
print "-------------------------------------------------------"
amptklib.SystemInfo()

amptkversion = amptklib.get_version()

#create output directory
if not os.path.exists(args.out):
    os.makedirs(args.out)
else:
    if not args.force:
        amptklib.log.error("Directory %s exists, add --force argument to overwrite" % args.out)
        sys.exit(1)
    else:
        shutil.rmtree(args.out)
        os.makedirs(args.out)

#parse a mapping file or a barcode fasta file, primers, etc get setup
#dealing with Barcodes, get ion barcodes or parse the barcode_fasta argument
barcode_file = os.path.join(args.out, args.out + ".barcodes_used.fa")
if os.path.isfile(barcode_file):
    os.remove(barcode_file)

#check if mapping file passed, use this if present, otherwise use command line arguments
if args.mapping_file:
    if not os.path.isfile(args.mapping_file):
        amptklib.error("Mapping file is not valid: %s" % args.mapping_file)
        sys.exit(1)
    amptklib.log.info("Parsing mapping file: %s" % args.mapping_file)
    mapdata = amptklib.parseMappingFile(args.mapping_file, barcode_file)
    #forward primer in first item in tuple, reverse in second
    FwdPrimer = mapdata[0]
    RevPrimer = mapdata[1]
    genericmapfile = args.mapping_file
else:
    if args.barcodes == 'pgm_barcodes.fa':
        #get script path and barcode file name
        pgm_barcodes = os.path.join(parentdir, 'DB', args.barcodes)
        if args.barcodes == "all":
            if args.multi == 'False':
                shutil.copyfile(pgm_barcodes, barcode_file)
            else:
                with open(barcode_file, 'w') as barcodeout:
                    with open(pgm_barcodes, 'rU') as input:
                        for rec in SeqIO.parse(input, 'fasta'):
                            outname = args.multi+'.'+rec.id
                            barcodeout.write(">%s\n%s\n" % (outname, rec.seq))
        else:
            bc_list = args.barcodes.split(",")
            inputSeqFile = open(pgm_barcodes, "rU")
            SeqRecords = SeqIO.to_dict(SeqIO.parse(inputSeqFile, "fasta"))
            for rec in bc_list:
                name = "BC." + rec
                seq = SeqRecords[name].seq
                if args.multi != 'False':
                    outname = args.multi+'.'+name
                else:
                    outname = name
                outputSeqFile = open(barcode_file, "a")
                outputSeqFile.write(">%s\n%s\n" % (outname, seq))
            outputSeqFile.close()
            inputSeqFile.close()
    else:
        shutil.copyfile(args.barcodes, barcode_file)
    
    #parse primers here so doesn't conflict with mapping primers
    #look up primer db otherwise default to entry
    if args.F_primer in amptklib.primer_db:
        FwdPrimer = amptklib.primer_db.get(args.F_primer)
    else:
        FwdPrimer = args.F_primer
    if args.R_primer in amptklib.primer_db:
        RevPrimer = amptklib.primer_db.get(args.R_primer)
    else:
        RevPrimer = args.R_primer

    #because we use an 'A' linker between barcode and primer sequence, add an A if ion is chemistry
    if args.platform == 'ion':
        FwdPrimer = 'A' + FwdPrimer
        Adapter = 'CCATCTCATCCCTGCGTGTCTCCGACTCAG'
    else:
        Adapter = ''


if args.platform != 'illumina':
    if not args.barcodes and not args.mapping_file:
        amptklib.log.error("For ion, 454, or illumina2 datasets you must specificy a multi-fasta file containing barcodes with -b, --barcode_fasta, or -m/--mapping_file")
        sys.exit(1)

if args.platform == 'illumina':
    #just need to get the correct .fastq.gz files into a folder by themselves
    #if illumina is selected, verify that args.fastq is a folder
    if not os.path.isdir(args.FASTQ):
        amptklib.log.error("%s is not a folder, for '--platform illumina', -i must be a folder containing raw reads" % (args.FASTQ))
        sys.exit(1)
    rawlist = []
    filelist = []
    for file in os.listdir(args.FASTQ):
        if file.endswith(".fastq.gz"):
            rawlist.append(file)
    if len(rawlist) > 0:
        if not '_R2' in sorted(rawlist)[1]:
            amptklib.log.info("Found %i single files, copying to %s folder" % (len(rawlist), args.out))
            filelist = rawlist
            for file in rawlist:
                shutil.copyfile(os.path.join(args.FASTQ,file),(os.path.join(args.out,file)))
        else:
            amptklib.log.info("Found %i paired-end files, copying to %s folder" % (len(rawlist) / 2, args.out))
            for file in rawlist:
                shutil.copyfile(os.path.join(args.FASTQ,file),(os.path.join(args.out,file)))
                if '_R1' in file:
                    filelist.append(file)

else:
    #count FASTQ records in input
    #start here to process the reads, first reverse complement the reverse primer
    ReverseCompRev = revcomp_lib.RevComp(RevPrimer)

    #if --names given, load into dictonary
    if args.names:
        amptklib.log.info("Parsing names for output files via %s" % args.names)
        namesDict = {}
        with open(args.names, 'rU') as input:
            for line in input:
                line = line.replace('\n', '')
                cols = line.split(',')
                if not cols[0] in namesDict:
                    namesDict[cols[0]] = cols[1]

    #load barcode fasta file into dictonary
    Barcodes = {}
    with open(barcode_file, 'rU') as input:
        for line in input:
            if line.startswith('>'):
                ID = line[1:-1].replace(' ', '')
                if args.names:
                    name = namesDict.get(ID)
                    if not name:
                        amptklib.log.error("%s not found in --names, keeping original name" % ID)
                        name = ID + ".fastq"
                    else:
                        name = name + ".fastq"          
                else:
                    name = ID + ".fastq"
                continue
            Barcodes[name]=line.strip()

    #count FASTQ records in input
    amptklib.log.info("Loading FASTQ Records")
    total = amptklib.countfastq(args.FASTQ)
    size = amptklib.checkfastqsize(args.FASTQ)
    readablesize = amptklib.convertSize(size)
    amptklib.log.info('{0:,}'.format(total) + ' reads (' + readablesize + ')')
    
    #output message depending on primer requirement
    if args.require_primer == 'off':   
        amptklib.log.info("Looking for %i barcodes" % (len(Barcodes)))
    elif args.require_primer == 'forward':
        amptklib.log.info("Looking for %i barcodes that must have FwdPrimer: %s" % (len(Barcodes), FwdPrimer))
    elif args.require_primer == 'both':
        amptklib.log.info("Looking for %i barcodes that must have FwdPrimer: %s and  RevPrimer: %s" % (len(Barcodes), FwdPrimer, RevPrimer))
    

    #this will loop through FASTQ file once, splitting those where barcodes are found, and primers trimmed
    runningTotal = 0
    trim = len(FwdPrimer)
    with open(args.FASTQ, 'rU') as input:
        for title, seq, qual in FastqGeneralIterator(input):
            Barcode, BarcodeLabel = FindBarcode(seq, Barcodes)
            if Barcode == "": #if not found, move onto next record
                continue
            BarcodeLength = len(Barcode)
            seq = seq[BarcodeLength:]
            qual = qual[BarcodeLength:]
            #look for forward primer
            if args.require_primer != 'off': #means we only want ones with forward primer and or reverse
                Diffs = primer.MatchPrefix(seq, FwdPrimer)
                if Diffs > args.primer_mismatch:
                    continue
                #if found, trim away primer
                seq = seq[trim:]
                qual = qual[trim:]
                if args.require_primer == 'both':
                    #look for reverse primer, strip if found
                    BestPosRev, BestDiffsRev = primer.BestMatch2(seq, ReverseCompRev, args.primer_mismatch)
                    if BestPosRev > 0:
                        seq = seq[:BestPosRev]
                        qual = qual[:BestPosRev]
                    else:
                        continue
            #check size
            if len(seq) < args.min_len: #filter out sequences less than minimum length.
                continue
            runningTotal += 1
            fileout = os.path.join(args.out, BarcodeLabel)
            with open(fileout, 'ab') as output:
                output.write("@%s\n%s\n+\n%s\n" % (title, seq, qual))
    if args.require_primer == 'off':   
        amptklib.log.info('{0:,}'.format(runningTotal) + ' total reads with valid barcode')
    elif args.require_primer == 'forward':
        amptklib.log.info('{0:,}'.format(runningTotal) + ' total reads with valid barcode and fwd primer')
    elif args.require_primer == 'both':
        amptklib.log.info('{0:,}'.format(runningTotal) + ' total reads with valid barcode and both primers')
    
    amptklib.log.info("Now Gzipping files")
    gzip_list = []
    for file in os.listdir(args.out):
        if file.endswith(".fastq"):
            gzip_list.append(file)

    for file in gzip_list:
        file_path = os.path.join(args.out, file)
        new_path = file_path + '.gz'
        with open(file_path, 'rU') as orig_file:
            with gzip.open(new_path, 'w') as zipped_file:
                zipped_file.writelines(orig_file)
        os.remove(file_path)
    
    #after all files demuxed into output folder, loop through and create SRA metadata file
    filelist = []
    for file in os.listdir(args.out):
        if file.endswith(".fastq.gz"):
            filelist.append(file)

amptklib.log.info("Finished: output in %s" % args.out)

#check for BioSample meta file
if args.biosample:
    amptklib.log.info("NCBI BioSample file detected, creating SRA metadata file") 
    #load in BioSample file to dictionary
    with open(args.biosample, 'rU') as input:
        reader = csv.reader(input, delimiter='\t')
        header = next(reader)
        acc = header.index('Accession')
        sample = header.index('Sample Name')
        bio = header.index('BioProject')     
        try:
            host = header.index('Host')
        except ValueError:
            host = header.index('Organism')
        BioDict = {col[sample]:(col[acc],col[bio],col[host]) for col in reader}
    #print BioDict
    #set some defaults based on the platform
    if args.platform == 'ion':
        header = 'bioproject_accession\tsample_name\tlibrary_ID\ttitle\tlibrary_strategy\tlibrary_source\tlibrary_selection\tlibrary_layout\tplatform\tinstrument_model\tdesign_description\tfiletype\tfilename\tbarcode\tforward_primer\treverse_primer\n'
        sequencer = 'ION_TORRENT'
        model = 'Ion Torrent PGM' 
        lib_layout = 'single'
    elif args.platform == '454':
        header = 'bioproject_accession\tsample_name\tlibrary_ID\ttitle\tlibrary_strategy\tlibrary_source\tlibrary_selection\tlibrary_layout\tplatform\tinstrument_model\tdesign_description\tfiletype\tfilename\tbarcode\tforward_primer\treverse_primer\n'
        sequencer = '_LS454'
        model = '454 GS FLX Titanium'
        lib_layout = 'single'
    elif args.platform == 'illumina':
        header = 'bioproject_accession\tsample_name\tlibrary_ID\ttitle\tlibrary_strategy\tlibrary_source\tlibrary_selection\tlibrary_layout\tplatform\tinstrument_model\tdesign_description\tfiletype\tfilename\tfilename2\tforward_barcode\treverse_barcode\tforward_primer\treverse_primer\n'
        sequencer = 'ILLUMINA'
        model = 'Illumina MiSeq'
        lib_layout = 'paired'
    else:
        amptklib.log.error("You specified a platform that is not supported")
        sys.exit(1)
    lib_strategy = 'AMPLICON'
    lib_source = 'GENOMIC'
    lib_selection = 'RANDOM PCR'
    filetype = 'fastq'
    
    #now open file for writing, input header and then loop through samples
    sub_out = args.out + '.submission.txt'
    with open(sub_out, 'w') as output:
        output.write(header)
        for file in filelist:
            if not args.description:
                description = '%s amplicon library was created using a barcoded fusion primer PCR protocol using Pfx50 polymerase (Thermo Fisher Scientific), size selected, and sequenced on the %s platform.  Sequence data was minimally processed, sequences were exported directly from the sequencing platform and only the barcode (index sequence) was trimmed prior to SRA submission. SRA submission generated with AMPtk %s' % (args.title, model, amptkversion.split(' ')[-1])
            else:
                description = args.description
            if args.platform == 'ion' or args.platform == '454': 
                name = file.split(".fastq")[0]
                if not name in BioDict: #lets try to look a bit harder, i.e. split on _ and - and look again
                    searchname = name.replace('-', '_')
                    searchname = searchname.split('_')[0]
                    if not searchname in BioDict: #if still not found, then skip
                        continue
                else:
                    searchname = name     
                bioproject = BioDict.get(searchname)[1]
                if not bioproject.startswith('PRJNA'):
                    bioproject = 'PRJNA'+bioproject
                sample_name = BioDict.get(searchname)[0]
                title = '%s amplicon sequencing of %s: sample %s' % (args.title, BioDict.get(name)[2], name)
                bc_name = file.split(".gz")[0]
                barcode_seq = Barcodes.get(bc_name)
                if args.append:
                    finalname = name+'_'+args.append
                    #also need to change the name for output files
                    newfile = file.replace(name, finalname)
                    os.rename(os.path.join(args.out, file), os.path.join(args.out, newfile))
                else:
                    finalname = name
                    newfile = file
                line = [bioproject,sample_name,finalname,title,lib_strategy,lib_source,lib_selection,lib_layout,sequencer,model,description,filetype,newfile,barcode_seq,FwdPrimer,RevPrimer]
            elif args.platform == 'illumina':
                name = file.split("_")[0]
                if not name in BioDict:
                    continue
                bioproject = BioDict.get(name)[1]
                if not bioproject.startswith('PRJNA'):
                    bioproject = 'PRJNA'+bioproject
                sample_name = BioDict.get(name)[0]
                title = '%s amplicon sequencing of %s: sample %s' % (args.title, BioDict.get(name)[2], name)   
                file2 = file.replace('_R1', '_R2')             
                #count number of _ in name, determines the dataformat
                fields = file.count("_")
                if fields > 3: #this is full illumina name with dual barcodes
                    dualBC = file.split("_")[1]
                    barcode_for = dualBC.split('-')[0]
                    barcode_rev = dualBC.split('-')[1]
                elif fields == 3: #this is older reverse barcoded name
                    barcode_for = 'None'
                    barcode_rev = file.split("_")[1]
                else:
                    barcode_for = 'missing'
                    barcode_rev = 'missing'
                if args.append:
                    finalname = name+'_'+args.append
                    newfile = file.replace(name, finalname)
                    newfile2 = file2.replace(name, finalname)
                    #also need to change the name for output files
                    os.rename(os.path.join(args.out, file), os.path.join(args.out, newfile1))
                    os.rename(os.path.join(args.out, file2), os.path.join(args.out, newfile2))
                    file = file.replace(name, finalname)
                else:
                    finalname = name
                    newfile = file
                    newfile2 = file2
                line = [bioproject,sample_name,finalname,title,lib_strategy,lib_source,lib_selection,lib_layout,sequencer,model,description,filetype,newfile,newfile2,barcode_for,barcode_rev,FwdPrimer,RevPrimer]
            #write output to file
            output.write('\t'.join(line)+'\n')
    amptklib.log.info("SRA submission file created: %s" % sub_out)

        
